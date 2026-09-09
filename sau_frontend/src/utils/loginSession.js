const LOGIN_ERROR_ACTIONS = Object.freeze({
  browser_launch_failed: '确认 Google Chrome 已安装；关闭残留的登录窗口后再试。',
  browser_closed: '重新开始登录，并保持软件打开的 Chrome 窗口处于运行状态。',
  login_page_unavailable: '检查网络并确认该平台登录页可以正常打开，然后重试。',
  login_timeout: '重新开始登录，并在平台规定时间内完成扫码、验证码和手机确认。',
  login_interrupted: '关闭残留的登录窗口后重新尝试，并保持浏览器窗口打开。',
  session_save_failed: '确认本机数据目录可写，并检查安全软件是否拦截了凭证保存。',
  unexpected_error: '重新尝试；如果仍然失败，请查看桌面日志并反馈下方错误代码。',
  operator_cancelled: '结束请求已接受；若平台登录窗口仍打开，请先手动关闭再重新开始。',
  cancel_pending: '若平台登录窗口仍打开，请手动关闭，然后检查关闭状态。',
  stream_disconnected: '确认软件仍在运行且网络没有切换，然后重新尝试。',
  login_not_completed: '关闭残留的登录窗口后重试，并完整完成平台侧确认。'
})

const cleanText = (value, fallback = '') => {
  const normalized = String(value ?? '')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
    .trim()
  return (normalized || fallback).slice(0, 360)
}

const cleanCode = value => {
  const normalized = String(value ?? '').trim().toLowerCase()
  return /^[a-z0-9_]{1,64}$/.test(normalized) ? normalized : 'unexpected_error'
}

const MACHINE_ACTION_COPY = Object.freeze({
  close_conflicting_browser_and_retry: LOGIN_ERROR_ACTIONS.browser_launch_failed,
  check_network_and_retry: LOGIN_ERROR_ACTIONS.login_page_unavailable,
  complete_login_then_retry: LOGIN_ERROR_ACTIONS.login_timeout,
  retry: LOGIN_ERROR_ACTIONS.login_interrupted,
  reopen_and_retry: LOGIN_ERROR_ACTIONS.browser_closed,
  none: LOGIN_ERROR_ACTIONS.operator_cancelled,
  check_data_directory_and_retry: LOGIN_ERROR_ACTIONS.session_save_failed,
  retry_and_check_logs: LOGIN_ERROR_ACTIONS.unexpected_error
})

const errorAction = (code, suppliedAction = '') => {
  const normalizedAction = cleanText(suppliedAction)
  if (/^[a-z0-9_]+$/i.test(normalizedAction)) {
    return MACHINE_ACTION_COPY[normalizedAction]
      || LOGIN_ERROR_ACTIONS[code]
      || LOGIN_ERROR_ACTIONS.unexpected_error
  }
  return normalizedAction || LOGIN_ERROR_ACTIONS[code] || LOGIN_ERROR_ACTIONS.unexpected_error
}

const normalizeQrCode = value => {
  const source = String(value ?? '').trim()
  if (/^data:image\/(?:png|jpe?g|webp);base64,[a-z0-9+/=\s]+$/i.test(source)) {
    return source
  }
  if (/^(?:iVBORw0KGgo|\/9j\/|UklGR)[a-z0-9+/=\s]{80,}$/i.test(source)) {
    const mimeType = source.startsWith('/9j/')
      ? 'image/jpeg'
      : (source.startsWith('UklGR') ? 'image/webp' : 'image/png')
    return `data:${mimeType};base64,${source.replace(/\s/g, '')}`
  }
  return ''
}

const parseStructuredEvent = payload => {
  const type = cleanText(payload?.type || payload?.event || payload?.status).toLowerCase()
  if (['error', 'failed', 'failure'].includes(type)) {
    const code = cleanCode(payload.code || payload.error_code)
    return {
      kind: 'failure',
      code,
      message: cleanText(payload.message, '平台没有确认登录。'),
      action: errorAction(code, payload.action || payload.next_action)
    }
  }
  if (['success', 'complete', 'completed'].includes(type)) {
    return {
      kind: 'success',
      message: cleanText(payload.message, '账号登录成功，本地凭证已经保存。')
    }
  }
  if (['qr', 'qrcode', 'qr_code'].includes(type)) {
    const dataUrl = normalizeQrCode(
      payload.data_url || payload.image_data_url || payload.qrcode?.image_data_url
    )
    return dataUrl
      ? { kind: 'qr_code', dataUrl, message: cleanText(payload.message) }
      : { kind: 'unknown' }
  }
  if (['stage', 'progress', 'manual', 'manual_login', 'session_started'].includes(type)) {
    const stage = cleanText(payload.stage || type).toLowerCase()
    const waitingForUser = [
      'manual',
      'manual_login',
      'waiting_user',
      'waiting_for_user',
      'waiting_for_account',
      'waiting_for_verification'
    ].includes(stage)
    return {
      kind: 'stage',
      stage: waitingForUser ? 'manual' : 'opening',
      message: cleanText(payload.message)
    }
  }
  return { kind: 'unknown' }
}

/**
 * Translate both the historical scalar SSE protocol and the structured login
 * protocol into a small UI state machine. Unknown text is deliberately ignored:
 * a long error or HTML response must never be rendered as a QR image.
 */
export const parseLoginSessionEvent = rawValue => {
  const value = String(rawValue ?? '').trim()
  if (!value) return { kind: 'unknown' }

  if (value.startsWith('{')) {
    try {
      return parseStructuredEvent(JSON.parse(value))
    } catch {
      return { kind: 'unknown' }
    }
  }

  if (value === 'MANUAL_LOGIN') {
    return { kind: 'stage', stage: 'manual', message: '' }
  }
  if (value === '200') {
    return { kind: 'success', message: '账号登录成功，本地凭证已经保存。' }
  }
  if (value === '500') {
    return {
      kind: 'failure',
      code: 'login_not_completed',
      message: '平台没有确认登录，或登录凭证未能保存。',
      action: LOGIN_ERROR_ACTIONS.login_not_completed
    }
  }

  if (value.startsWith('ERROR:')) {
    const separator = value.indexOf(':', 6)
    const code = cleanCode(separator === -1 ? value.slice(6) : value.slice(6, separator))
    const message = cleanText(
      separator === -1 ? '' : value.slice(separator + 1),
      '平台登录流程没有完成。'
    )
    return { kind: 'failure', code, message, action: errorAction(code) }
  }

  const dataUrl = normalizeQrCode(value)
  return dataUrl ? { kind: 'qr_code', dataUrl, message: '' } : { kind: 'unknown' }
}

export const buildStreamFailure = () => ({
  kind: 'failure',
  code: 'stream_disconnected',
  message: '软件与登录流程的状态连接已中断。',
  action: LOGIN_ERROR_ACTIONS.stream_disconnected
})

/**
 * Resolve the cancellation endpoint without guessing whether credentials were
 * saved. A cancel request can race a successful login, so outcome is the
 * authoritative signal and `active` only describes cleanup still in progress.
 */
export const resolveLoginCancellation = response => {
  const data = response?.data && typeof response.data === 'object' ? response.data : {}
  const outcome = cleanText(data.outcome, 'unknown').toLowerCase()
  const message = cleanText(response?.msg)

  if (outcome === 'success') {
    return { kind: 'success', message: message || '账号登录已经完成。' }
  }
  if (data.active === true) {
    return {
      kind: 'pending',
      code: 'cancel_pending',
      message: message || '结束请求已接受，后台仍在关闭登录窗口。',
      action: LOGIN_ERROR_ACTIONS.cancel_pending
    }
  }
  if (outcome === 'cancelled') {
    return {
      kind: 'cancelled',
      code: 'operator_cancelled',
      message: message || '本次登录已经结束。',
      action: LOGIN_ERROR_ACTIONS.operator_cancelled
    }
  }
  if (outcome === 'failed') {
    return {
      kind: 'failure',
      code: 'login_not_completed',
      message: message || '平台登录流程未能完成。',
      action: LOGIN_ERROR_ACTIONS.login_not_completed
    }
  }
  return {
    kind: 'review',
    code: 'outcome_unknown',
    message: '登录流程已结束，请刷新确认账号状态。',
    action: '查看媒体账号列表；若账号已经出现或状态已更新，无需重新登录。'
  }
}
