import { sameBrand } from './geoJourney.js'

export const searchProjectSnapshot = project => project ? {
  id:project.id,name:project.name,website:project.website,entity_type:project.entity_type
} : null

export const sameSearchProject = (left,right) => Boolean(left && right && left.id===right.id && sameBrand(left,right))
const matchingRecord = (record,project) => Boolean(project && sameBrand(record?.profile,project))

// A shared native worker is not proof of ownership. A task started on this page
// may be bound before results exist; any returned identity must still match.
export function searchTaskMatchesProject(task,project,binding=null) {
  if(!task?.id || !project)return false
  const records=[task.result?.plan,task.result?.report].filter(Boolean)
  if(records.some(record=>!matchingRecord(record,project)))return false
  return records.length>0 || Boolean(binding?.taskId===task.id && sameSearchProject(binding.project,project))
}

// Imported reports/plans may be run without selecting a stored project. Only
// this page's explicit start may bind that context; a global latest task cannot.
export function searchTaskMatchesContext(task,project,binding,intent) {
  if(project)return searchTaskMatchesProject(task,project,binding)
  if(!task?.id || binding?.taskId!==task.id || binding.project!==null || binding.intent!==intent
    || !sameBrand(binding.profile,binding.profile))return false
  return searchTaskMatchesProject(task,binding.profile,{taskId:task.id,project:binding.profile})
}

export function restoredSearchRecords({saved={},project,currentReport=null,explicitReport=false,task=null}) {
  // An imported workspace need not have a stored project. Restore its own
  // complete identities, but never attach the shared worker to that workspace.
  if(!project)return {
    report:explicitReport && currentReport?currentReport
      : sameBrand(saved.report?.profile,saved.report?.profile)?saved.report:null,
    plan:sameBrand(saved.plan?.profile,saved.plan?.profile)?saved.plan:null,
  }
  const report=explicitReport && currentReport ? currentReport
    : matchingRecord(saved.report,project)?saved.report
      : matchingRecord(task?.result?.report,project)?task.result.report
        : matchingRecord(currentReport,project)?currentReport:null
  const plan=matchingRecord(saved.plan,project)?saved.plan
    : matchingRecord(task?.result?.plan,project)?task.result.plan:null
  return {report,plan}
}

// A native run can finish normally while its report says collection stopped.
// Preserve that report state; never infer whether a question was sent from it.
export function searchTaskRecoverySource(task) {
  const report=task?.result?.report
  if(task?.status!=='completed' || !['stopped','failed','needs_review','need_action'].includes(report?.status))return task
  return {...task,status:report.status,recovery:{...task.recovery,
    failure_reason:task.recovery?.failure_reason || report.stop_reason,
    requested_client_mode:task.recovery?.requested_client_mode || report.execution?.client_mode,
    requested_model_label:task.recovery?.requested_model_label || report.execution?.requested_model_label || ''}}
}
