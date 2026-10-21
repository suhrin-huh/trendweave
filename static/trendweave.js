const {createApp, ref, reactive, nextTick, watch, onMounted, onBeforeUnmount} = Vue;
createApp({setup() {
  const exampleRequest = '한국의 20-30대 고객이 일상에서 활용하기 쉬운 상품 중심으로 런웨이·컬러·시장·브랜드 트렌드를 분석하고 상품기획 방향 3개를 제안해줘.';
  const brand = ref('ZARA');
  const season = ref('2027 S/S'), input = ref(''), sending = ref(false), ready = ref(false);
  const configurationError = ref(''), messages = ref([]), selected = ref(null), logEl = ref(null), modalEl = ref(null), inputEl = ref(null);
  const nearBottom = ref(true), hasNewMessages = ref(false);
  const seasons = ['2026 F/W','2027 S/S','2027 F/W','2028 S/S','2028 F/W'];
  // A page load always starts a fresh conversation. Remove keys written by
  // earlier versions, but leave unrelated application storage untouched.
  sessionStorage.removeItem('trendweave-thread');
  sessionStorage.removeItem('trendweave-active');
  const thread = crypto.randomUUID();
  let stream = null, poll = null, counter = 0, activeItem = null;
  const axisLabel = key => ({runway:'런웨이',color:'컬러',market:'시장',brand:'브랜드'}[key] || key);
  const statusLabel = status => ({queued:'준비 중',running:'분석 중',completed:'완료',failed:'생성 실패'}[status] || status);
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
    Object.assign(item, {status:data.status, result:data.result, report_html:data.report_html, error:data.error, brand:data.result?.brand || data.result?.report?.brand || data.request?.brand || item.brand});
    if (data.status === 'completed' || data.status === 'failed') {
      if (activeItem === item) {
        stopConnection(); activeItem=null; sending.value=false;
        if (wasPending) focusInput();
      }
      item.connectionMessage='';
      if (wasPending) scrollToLatest();
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
      if (id <= item.lastEventId) return;
      item.lastEventId=id; item.events.push({...data,id}); item.latest=data.message;
      if (!['completed','failed'].includes(data.step)) item.status='running';
      else {try {await refresh(item);} catch {item.connectionMessage='결과를 불러오지 못했습니다.';}}
      scrollToLatest();
    };
    stream.onerror = () => {item.connectionMessage='연결을 복구하고 있습니다. 진행 중인 분석은 계속됩니다.';};
    // Polling also recovers when a proxy drops the terminal SSE frame.
    poll=setInterval(async () => {try {await refresh(item);} catch {item.connectionMessage='서버 연결을 확인해 주세요.';}}, 5000);
  }
  function newItem(request, reportSeason) {
    return reactive({id:++counter,kind:'report',brand:brand.value,season:reportSeason,request,status:'queued',events:[],lastEventId:0,latest:'',connectionMessage:'',result:null,report_html:'',error:'',downloading:false,pdfError:''});
  }
  async function generate(request, requestedSeason=season.value) {
    if(sending.value || !ready.value || !request.trim()) return;
    sending.value=true;
    messages.value.push({id:++counter,kind:'user',text:`${requestedSeason} · ${brand.value}\n${request}`});
    const item=newItem(request,requestedSeason); messages.value.push(item); input.value=''; scrollToLatest({force: true});
    try {
      const result=await (await api('/reports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({season:requestedSeason,planning_request:request,thread_id:thread})})).json();
      item.report_id=result.report_id;
      connect(item);
    } catch(error) {item.status='failed';item.error=error.message;sending.value=false;scrollToLatest();focusInput();}
  }
  function submit() {generate(input.value.trim());}
  function onInputKeydown(event) {
    // IME confirmation must not submit a half-composed Korean message.
    if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    submit();
  }
  function retry(item) {generate(item.request,item.season);}
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
  return {brand,season,seasons,input,sending,ready,configurationError,messages,selected,logEl,modalEl,inputEl,hasNewMessages,onConversationScroll,scrollToLatest,onInputKeydown,exampleRequest,axisLabel,statusLabel,generate,submit,retry,reconnect,downloadPdf};
}}).mount('#app');
