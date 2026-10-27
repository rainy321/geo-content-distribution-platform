import {http} from '@/utils/request'
const path=(cycleId,probeId)=>`/api/geo-content/cycles/${cycleId}/probes/${probeId}/business-reviews`
export const geoBusinessReviewApi={
  get:(cycleId,probeId)=>http.get(path(cycleId,probeId),undefined,{suppressGlobalError:true}),
  save:(cycleId,probeId,data)=>http.post(path(cycleId,probeId),data,{timeout:20000,suppressGlobalError:true})
}
