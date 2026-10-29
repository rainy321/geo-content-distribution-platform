import {http} from '@/utils/request'
const path=projectId=>`/api/geo-content/projects/${projectId}/progress`
const options={timeout:20000,suppressGlobalError:true}
export const geoOptimizationProgressApi={
  get:(projectId,query={})=>http.get(path(projectId),query,options),
  append:(projectId,payload)=>http.post(path(projectId),payload,options)
}
