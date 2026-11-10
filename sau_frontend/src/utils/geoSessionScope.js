const owns=(value,key)=>Boolean(value && typeof value==='object' && Object.hasOwn(value,key))
const modes=new Set(['consumer','work'])
const invalid='账号会话范围无效，请核对原记录或选择；未打开其他会话。'

export function normalizeSessionSlot(slot='primary',mode='consumer'){
  if(!['primary','secondary'].includes(slot)||!modes.has(mode)||slot==='secondary'&&mode!=='consumer')throw Error(invalid)
  return slot
}

const recordScopes=record=>record?.baseline_source==='user_reported_external'?[]:[
  record?.baseline?.execution,record?.execution,record?.result?.report?.execution,
  record?.result?.plan?.execution,record?.result?.probe?.execution,record?.options,record,
  record?.result,record?.observation,record?.result?.observation,record?.result?.login,
  record?.result?.login?.observation,record?.result?.login_observation,record?.recovery,
].filter(value=>value && typeof value==='object' && !Array.isArray(value))

// Session metadata belongs to collection execution, never the brand profile or
// the frozen questions. Legacy records carry no additional fields.
export function sessionScopeForRecord(record){
  const scopes=recordScopes(record),stored=scopes.filter(value=>owns(value,'session_slot'))
  const profiles=scopes.filter(value=>owns(value,'profile_mode'))
  if(profiles.some(value=>!modes.has(value.profile_mode))||new Set(profiles.map(value=>value.profile_mode)).size>1)throw Error(invalid)
  const profile=profiles[0]
  const profileMode=profile?.profile_mode
  const policies=scopes.filter(value=>owns(value,'mode_policy'))
  if(policies.some(value=>value.mode_policy!=='available_web')||policies.length&&!profile)throw Error(invalid)
  if(!stored.length)return {}
  const observedModes=scopes.flatMap(value=>['profile_mode','client_mode','requested_client_mode','actual_client_mode']
    .filter(key=>owns(value,key)&&value[key]!=null).map(key=>value[key]))
  if(observedModes.some(value=>!modes.has(value)))throw Error(invalid)
  const slots=stored.map(value=>{
    const slot=normalizeSessionSlot(value.session_slot)
    for(const mode of observedModes)normalizeSessionSlot(slot,mode)
    return slot
  })
  if(new Set(slots).size!==1)throw Error(invalid)
  const scope={session_slot:slots[0]}
  if(profile){
    if(!modes.has(profileMode))throw Error(invalid)
    scope.profile_mode=profileMode
  }
  if(policies.length)scope.mode_policy='available_web'
  return scope
}

export const sessionSlotForRecord=record=>sessionScopeForRecord(record).session_slot||'primary'
export function sessionSlotMatchesRecord(record,slot='primary',mode='consumer'){
  try{return sessionSlotForRecord(record)===normalizeSessionSlot(slot,mode)}catch{return false}
}
