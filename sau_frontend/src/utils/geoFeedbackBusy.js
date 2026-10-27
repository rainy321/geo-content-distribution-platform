export function updateFeedbackBusy(owners,event) {
  if(!(owners instanceof Set)||typeof event?.owner!=='string'||!event.owner||event.owner.length>200||typeof event.active!=='boolean')return owners
  const next=new Set(owners)
  if(event.active)next.add(event.owner)
  else next.delete(event.owner)
  return next
}
