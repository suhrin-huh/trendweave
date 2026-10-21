const {createApp, ref, reactive, nextTick, watch, onMounted, onBeforeUnmount} = Vue;
createApp({setup() {
  const exampleRequest = '한국의 20-30대 고객이 일상에서 활용하기 쉬운 상품 중심으로 런웨이·컬러·시장·브랜드 트렌드를 분석하고 상품기획 방향 3개를 제안해줘.';
  const brand = ref('ZARA');
  const input = ref(''), sending = ref(false), ready = ref(false), pendingPreview = ref(null);
  const configurationError = ref(''), messages = ref([]), selected = ref(null), logEl = ref(null), modalEl = ref(null), inputEl = ref(null);
  const nearBottom = ref(true), hasNewMessages = ref(false);
  // A page load always starts a fresh conversation. Remove keys written by
  // earlier versions, but leave unrelated application storage untouched.
  sessionStorage.removeItem('trendweave-thread');
  sessionStorage.removeItem('trendweave-active');
  const thread = crypto.randomUUID();
  let stream = null, poll = null, counter = 0, activeItem = null;
  const historyElements = new Map();
  const axisLabel = key => ({runway:'런웨이',color:'컬러',market:'시장',brand:'브랜드'}[key] || key);
  const stepLabel = key => ({queued:'분석 대기',normalizer:'요청 정리',planner:'검색 계획',search:'자료 검색',grade:'문서 평가',rewrite:'재검색 계획',extract:'신호 추출',strategy:'전략 작성',critic:'전략 검토',report:'보고서 작성',completed:'분석 완료',failed:'분석 실패',error:'자료 수집 오류'}[key] || key);
  const statusLabel = status => ({queued:'준비 중',running:'분석 중',completed:'완료',failed:'생성 실패'}[status] || status);
  function setHistoryElement(id, el) {
    if (el) historyElements.set(id, el);
    else historyElements.delete(id);
  }
  function appendEvents(item, incoming) {
    const el = historyElements.get(item.id);
    const follow = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    const events = new Map(item.events.map(event => [event.id, event]));
    let added = 0;
    for (const event of incoming || []) {
      const id = Number(event.id);
      if (!Number.isInteger(id) || id < 1 || events.has(id) || typeof event.message !== 'string') continue;
      events.set(id, {...event, id});
      added++;
    }
    if (!added) return false;
    item.events = [...events.values()].sort((a, b) => a.id - b.id);
    const latest = item.events[item.events.length - 1];
    item.lastEventId = latest.id;
    item.latest = latest.message;
    nextTick(() => {
      const current = historyElements.get(item.id);
      if (follow && item.historyExpanded && current) current.scrollTop = current.scrollHeight;
    });
    return true;
  }
  async function toggleHistory(item) {
    item.historyExpanded = !item.historyExpanded;
    if (item.historyExpanded) {
      await nextTick();
      const el = historyElements.get(item.id);
      if (el) el.scrollTop = el.scrollHeight;
    }
  }
  function onConversationScroll() {
    const el = logEl.value;
    if (!el) return;
    nearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom.value) hasNewMessages.value = false;
  }
  async function scrollToLatest({force = false} = {}) {
    const follow = force || nearBottom.value;
    await nextTick();
    if (!logEl.value) return;
    if (follow) {
      logEl.value.scrollTop = logEl.value.scrollHeight;
      nearBottom.value = true;
      hasNewMessages.value = false;
    } else {
      hasNewMessages.value = true;
    }
  }
  async function focusInput() {
    await nextTick();
    if (!selected.value) inputEl.value?.focus({preventScroll: true});
  }
  async function api(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let body; try {body = await response.json();} catch {body = {};}
      throw new Error(typeof body.detail === 'string' ? body.detail : '요청을 처리하지 못했습니다. 입력 내용과 서버 연결을 확인해 주세요.');
    }
    return response;
  }
  function stopConnection() {if(stream) stream.close(); stream=null; if(poll) clearInterval(poll); poll=null;}
  async function refresh(item) {
    const data = await (await api(`/reports/${item.report_id}`)).json();
    const wasPending = item.status === 'queued' || item.status === 'running';
    const historyChanged = appendEvents(item, data.events);
    Object.assign(item, {status:data.status, result:data.result, report_html:data.report_html, error:data.error, brand:data.result?.brand || data.result?.report?.brand || data.request?.brand || item.brand});
    if (data.status === 'completed' || data.status === 'failed') {
      if (activeItem === item) {
        stopConnection(); activeItem=null; sending.value=false;
        if (wasPending) focusInput();
      }
      item.connectionMessage='';
      if (wasPending) scrollToLatest();
    } else if (historyChanged) {
      scrollToLatest();
    }
    return data;
  }
  async function reconnect(item) {
    try {await refresh(item); if(['queued','running'].includes(item.status)) connect(item);}
    catch {item.connectionMessage='서버에 연결할 수 없습니다. 분석 상태를 다시 확인해 주세요.';}
  }
  function connect(item) {
    stopConnection(); activeItem=item; sending.value=true;
    stream = new EventSource(`/reports/${item.report_id}/events?after=${item.lastEventId || 0}`);
    stream.onmessage = async event => {
      item.connectionMessage='';
      if(event.data === '[DONE]') {try {await refresh(item);} catch {item.connectionMessage='결과를 불러오지 못했습니다.';} return;}
      let data; try {data=JSON.parse(event.data);} catch {return;}
      const id = Number(event.lastEventId);
      if (!appendEvents(item, [{...data, id}])) return;
      if (['completed','failed'].includes(data.step)) {
        try {await refresh(item);} catch {item.connectionMessage='결과를 불러오지 못했습니다.';}
      } else if (!['completed','failed'].includes(item.status)) item.status='running';
      scrollToLatest();
    };
    stream.onerror = () => {item.connectionMessage='연결을 복구하고 있습니다. 진행 중인 분석은 계속됩니다.';};
    // Polling also recovers when a proxy drops the terminal SSE frame.
    poll=setInterval(async () => {try {await refresh(item);} catch {item.connectionMessage='서버 연결을 확인해 주세요.';}}, 5000);
  }
  function newItem(request, reportSeason) {
    return reactive({id:++counter,kind:'report',brand:brand.value,season:reportSeason,request,status:'queued',events:[],historyExpanded:true,lastEventId:0,latest:'',connectionMessage:'',result:null,report_html:'',error:'',downloading:false,pdfError:''});
  }
  async function generate(request) {
    if(sending.value || pendingPreview.value || !ready.value || !request.trim()) return;
    sending.value=true;
    messages.value.push({id:++counter,kind:'user',text:`${brand.value}\n${request}`});
    const item=reactive({id:++counter,kind:'preview',status:'planning',request,preview:null,suggestedSeason:'',error:''});
    messages.value.push(item); pendingPreview.value=item; input.value=''; scrollToLatest({force:true});
    let followPreview=false;
    try {
      const data=await (await api('/reports/preview',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({planning_request:request,thread_id:thread})})).json();
      followPreview=nearBottom.value;
      item.preview=data; item.suggestedSeason=data.season; item.status='review';
    } catch(error) {
      item.status='error'; item.error=error.message; pendingPreview.value=null;
    } finally {
      sending.value=false;
      if (item.status==='review') {
        await nextTick();
        if (followPreview) {
          const heading=document.getElementById('review-heading-'+item.id);
          heading?.scrollIntoView({block:'start'});
          heading?.focus({preventScroll:true});
        } else scrollToLatest();
      } else {scrollToLatest();focusInput();}
    }
  }
  async function approvePreview(item) {
    if(item.status!=='review' || sending.value) return;
    const match=/^(20\d{2})\s*(S\/?S|F\/?W)$/i.exec(item.preview.season.trim());
    if(!match || !item.preview.planning_request.trim() || !item.preview.report_direction.trim()) {
      item.error='시즌은 YYYY S/S 또는 YYYY F/W 형식으로 입력하고 요청과 보고서 방향을 채워 주세요.';
      return;
    }
    item.preview.season=`${match[1]} ${match[2].toUpperCase().startsWith('S')?'S/S':'F/W'}`;
    item.status='approving'; item.error=''; sending.value=true;
    try {
      const response=await (await api('/reports',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({preview_id:item.preview.preview_id,approved:true,thread_id:thread,
          season:item.preview.season,brand:item.preview.brand,
          planning_request:item.preview.planning_request.trim(),report_direction:item.preview.report_direction.trim()})})).json();
      item.status='approved'; pendingPreview.value=null;
      const report=newItem(item.preview.planning_request.trim(),item.preview.season);
      report.report_id=response.report_id; report.brand=item.preview.brand;
      messages.value.push(report);scrollToLatest({force:true});connect(report);
    } catch(error) {item.status='review';item.error=error.message;sending.value=false;}
  }
  async function cancelPreview(item) {
    if(item.status!=='review' || sending.value) return;
    item.status='cancelling';item.error='';sending.value=true;
    try {
      await api(`/reports/preview/${item.preview.preview_id}/cancel`,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({thread_id:thread})});
      item.status='cancelled';pendingPreview.value=null;focusInput();
    } catch(error) {item.status='review';item.error=error.message;}
    finally {sending.value=false;}
  }
  const requestTime = value => value ? value.slice(0,16).replace('T',' ')+' (한국시간)' : '';
  function submit() {generate(input.value.trim());}
  function onInputKeydown(event) {
    // IME confirmation must not submit a half-composed Korean message.
    if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    submit();
  }
  function retry(item) {generate(item.request);}
  async function downloadPdf(item) {
    if(item.downloading) return;
    item.downloading=true; item.pdfError='';
    try {
      const response=await api(`/reports/${item.report_id}/pdf`);
      const blob=await response.blob(), url=URL.createObjectURL(blob), a=document.createElement('a');
      a.href=url; a.download=`TrendWeave_${item.brand}_${item.season.replace(/\W/g,'')}_${item.report_id}.pdf`;
      document.body.append(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url),10000);
    } catch(error) {item.pdfError=error.message;} finally {item.downloading=false;}
  }
  let previousFocus;
  watch(selected, async value => {
    document.body.style.overflow=value?'hidden':'';
    if(value) {previousFocus=document.activeElement;await nextTick();modalEl.value?.focus();}
    else previousFocus?.focus();
  });
  function modalKeys(event) {
    if(!selected.value) return;
    if(event.key==='Escape') selected.value=null;
    if(event.key==='Tab') {
      const elements=modalEl.value.querySelectorAll('button:not(:disabled),a[href],summary');
      const first=elements[0],last=elements[elements.length-1];
      if(event.shiftKey && (document.activeElement===first || document.activeElement===modalEl.value)) {event.preventDefault();last?.focus();}
      else if(!event.shiftKey && document.activeElement===last) {event.preventDefault();first?.focus();}
    }
  }
  onMounted(async () => {
    document.addEventListener('keydown',modalKeys);
    try {
      const health=await (await api('/health')).json(); brand.value=health.brand || 'ZARA'; ready.value=health.analysis_ready === true;
      configurationError.value=health.configuration_error || (ready.value?'':'패션 분석 서버가 준비되지 않았습니다. 서버 설정을 확인하세요.');
    } catch {configurationError.value='분석 서버에 연결할 수 없습니다. 서버 실행 상태를 확인하고 새로고침해 주세요.';}
  });
  onBeforeUnmount(()=>{stopConnection();document.removeEventListener('keydown',modalKeys);});
  return {brand,input,sending,ready,pendingPreview,approvePreview,cancelPreview,requestTime,configurationError,messages,selected,logEl,modalEl,inputEl,hasNewMessages,onConversationScroll,scrollToLatest,onInputKeydown,exampleRequest,axisLabel,stepLabel,statusLabel,setHistoryElement,toggleHistory,generate,submit,retry,reconnect,downloadPdf};
}}).mount('#app');
