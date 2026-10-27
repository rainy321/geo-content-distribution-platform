import {http} from '@/utils/request'
export const geoWebsiteAuditApi={
  get:id=>http.get(`/api/geo-content/cycles/${id}/website-audit`,undefined,{suppressGlobalError:true}),
  run:(id,data)=>http.post(`/api/geo-content/cycles/${id}/website-audit`,data,{timeout:65000,suppressGlobalError:true})
}
