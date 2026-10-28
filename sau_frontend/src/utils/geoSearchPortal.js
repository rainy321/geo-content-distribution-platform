import {searchProviders} from './geoSearchOnboarding.js'

const purposes=['entry','help','submit']
const errorMessages=Object.freeze({
  invalid_search_portal:'请选择已提供的官方搜索入口；也可复制地址到日常浏览器。',
  browser_bridge_unavailable:'浏览器打开服务尚未就绪，请复制地址到日常浏览器。',
  browser_request_rejected:'系统未接受浏览器打开请求，请复制地址到日常浏览器。',
  browser_request_unconfirmed:'浏览器打开请求未确认，请先检查日常浏览器，也可复制地址自行打开。',
  browser_request_timeout:'浏览器打开请求未收到回执，打开状态未知。请先检查日常浏览器，也可复制地址自行打开。'
})
const knownCode=code=>typeof code==='string'&&Object.hasOwn(errorMessages,code)
const requestError=code=>Object.assign(new Error(errorMessages[code]),{code})
export const searchPortalErrorMessage=cause=>errorMessages[knownCode(cause?.code)?cause.code:'browser_request_unconfirmed']
export const searchPortalUrl=(provider,purpose)=>typeof provider==='string'&&typeof purpose==='string'&&Object.hasOwn(searchProviders,provider)&&purposes.includes(purpose)?searchProviders[provider][purpose]:null
const desktopHost=host=>Boolean(host?.pywebview)||[host?.location?.search,host?.location?.hash?.split('?')[1]].some(query=>new URLSearchParams(query||'').get('desktop')==='1')

export function openSearchPortal(event,provider,purpose,host=globalThis.window){
  // Keep the anchor's normal browser behavior in the web version. Intercept
  // synchronously in the desktop, before WebView2 handles target=_blank.
  if(!desktopHost(host))return null
  event.preventDefault()
  const url=searchPortalUrl(provider,purpose),api=host?.pywebview?.api
  if(!url)return Promise.reject(requestError('invalid_search_portal'))
  if(typeof api?.open_search_portal!=='function')return Promise.reject(requestError('browser_bridge_unavailable'))
  return requestPortal(api,provider,purpose,url)
}

async function requestPortal(api,provider,purpose,url){
  let timer
  try{
    const result=await Promise.race([
      // The native boundary logs exceptions. Never echo or log a rejected
      // bridge message here: it may contain a local path or traceback.
      Promise.resolve().then(()=>api.open_search_portal(provider,purpose)).catch(()=>{throw requestError('browser_request_unconfirmed')}),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(requestError('browser_request_timeout')),8000)})
    ])
    if(result?.launch_requested!==true){
      const reason=result?.launch_requested===false&&['invalid_search_portal','browser_request_rejected','browser_request_unconfirmed'].includes(result.reason)?result.reason:'browser_request_unconfirmed'
      throw requestError(reason)
    }
    if(result.provider!==provider||result.purpose!==purpose||result.url!==url)throw requestError('browser_request_unconfirmed')
    return result
  }finally{clearTimeout(timer)}
}
