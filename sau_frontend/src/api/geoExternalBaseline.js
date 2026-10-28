import {http} from '@/utils/request'
const options={timeout:20000,suppressGlobalError:true}
export const geoExternalBaselineApi={
  list:(projectId,offset=0)=>http.get('/api/geo-content/external-baseline-plans',{project_id:projectId,offset},options),
  prepare:data=>http.post('/api/geo-content/external-baseline-plans/prepare',data,options),
  freeze:data=>http.post('/api/geo-content/external-baseline-plans/freeze',data,options),
  create:data=>http.post('/api/geo-content/external-cycles',data,options)
}
