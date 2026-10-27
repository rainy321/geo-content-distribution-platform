// Private drafts live only in this running application. No browser storage.
export const feedbackSessionLimits = Object.freeze({entries:24,bytes:8*1024*1024})
export const feedbackCapacityMessage = '本次应用的反馈草稿容量已满，当前填写仍在窗口中；离开前请导出或自行保留。原草稿和未确认保存不会被覆盖。'
export const feedbackConflictMessage = '同一条反馈的草稿已在另一入口更新，当前填写未覆盖原草稿；请先导出或自行保留，再重新打开核对。'
const identifier=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value)
export function feedbackCheckpointKey(kind,scope,probeId=null) {
  if(!['external','context','business'].includes(kind)||!identifier(scope?.cycleId)
    ||scope.projectId!==null&&(!Number.isSafeInteger(scope.projectId)||scope.projectId<1)
    ||kind!=='external'&&!identifier(probeId))return null
  return `${kind}:${scope.projectId??'unassigned'}:${scope.cycleId}${probeId?':'+probeId:''}`
}

export function createFeedbackSession(limits=feedbackSessionLimits) {
  const records=new Map();let used=0,sequence=0,owners=0
  return {
    owner(kind){return `feedback-${kind}-${++owners}`},
    read(key){const entry=records.get(key);return entry?{token:entry.token,version:entry.version,value:JSON.parse(entry.json)}:null},
    write(key,value,token=null,version=null){
      const prior=records.get(key)
      if(!key||token!==null&&prior?.token!==token||version!==null&&prior?.version!==version)return {ok:false,reason:'stale'}
      let json
      try{json=JSON.stringify(value)}catch{return {ok:false,reason:'invalid'}}
      if(typeof json!=='string')return {ok:false,reason:'invalid'}
      const bytes=new TextEncoder().encode(json).length
      if(!prior&&records.size>=limits.entries||used-(prior?.bytes||0)+bytes>limits.bytes)return {ok:false,reason:'capacity'}
      if(prior?.json===json)return {ok:true,token:prior.token,version:prior.version}
      const next={token:prior?.token??++sequence,version:(prior?.version||0)+1,json,bytes}
      records.set(key,next);used=used-(prior?.bytes||0)+bytes
      return {ok:true,token:next.token,version:next.version}
    },
    settle(key,token,payload,value){
      const prior=records.get(key)
      if(!prior||prior.token!==token||JSON.stringify(JSON.parse(prior.json).frozen)!==JSON.stringify(payload))return false
      return this.write(key,value,token,prior.version).ok
    },
    confirmed(key,token,payload=undefined){
      const prior=records.get(key)
      if(!prior||prior.token!==token||payload!==undefined&&JSON.stringify(JSON.parse(prior.json).frozen)!==JSON.stringify(payload))return false
      records.delete(key);used-=prior.bytes;return true
    },
    stats(){return {entries:records.size,bytes:used,limits:{...limits}}}
  }
}
export const feedbackSession=createFeedbackSession()

export function exportFeedbackDraft(value,name='geo-feedback-draft') {
  const href=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'}))
  const link=document.createElement('a');link.href=href;link.download=`${name}.json`;link.click()
  setTimeout(()=>URL.revokeObjectURL(href),1000)
}
