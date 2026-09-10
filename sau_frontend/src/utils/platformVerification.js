const VERIFICATION_DESTINATIONS = Object.freeze({
  zhihu: Object.freeze({
    label: '知乎内容管理',
    url: 'https://www.zhihu.com/creator/content-management/all',
    pathPrefix: '/creator/content-management'
  }),
  toutiao: Object.freeze({
    label: '今日头条内容管理',
    url: 'https://mp.toutiao.com/profile_v4/manage/content',
    pathPrefix: '/profile_v4/manage/content'
  }),
  baijiahao: Object.freeze({
    label: '百家号内容管理',
    url: 'https://baijiahao.baidu.com/builder/rc/content',
    pathPrefix: '/builder/rc/content'
  }),
  sohu: Object.freeze({
    label: '搜狐号内容管理',
    url: 'https://mp.sohu.com/mpfe/v4/contentManagement/first/page',
    pathPrefix: '/mpfe/v4/contentManagement'
  }),
  xiaohongshu: Object.freeze({
    label: '小红书笔记管理',
    url: 'https://creator.xiaohongshu.com/new/note-manager',
    pathPrefix: '/new/note-manager'
  }),
  douyin: Object.freeze({
    label: '抖音作品管理',
    url: 'https://creator.douyin.com/creator-micro/content/manage',
    pathPrefix: '/creator-micro/content/manage'
  }),
  kuaishou: Object.freeze({
    label: '快手作品管理',
    url: 'https://cp.kuaishou.com/article/manage/video?status=2&from=publish',
    pathPrefix: '/article/manage/video'
  }),
  bilibili: Object.freeze({
    label: 'Bilibili 稿件管理',
    url: 'https://member.bilibili.com/platform/upload-manager/article',
    pathPrefix: '/platform/upload-manager/article'
  }),
  channels: Object.freeze({
    label: '视频号内容管理',
    url: 'https://channels.weixin.qq.com/platform/post/list',
    pathPrefix: '/platform/post/list'
  }),
  tiktok: Object.freeze({
    label: 'TikTok Studio',
    // The Studio root is the stable official entry. TikTok may redirect to a
    // localized or account-specific content path after authentication.
    url: 'https://www.tiktok.com/tiktokstudio',
    pathPrefix: '/tiktokstudio'
  })
})

const PUBLIC_CONTENT_RULES = Object.freeze({
  zhihu: Object.freeze([
    Object.freeze({ host: 'www.zhihu.com', path: /^\/p\/[1-9]\d*\/?$/ }),
    Object.freeze({ host: 'zhuanlan.zhihu.com', path: /^\/p\/[1-9]\d*\/?$/ })
  ]),
  toutiao: Object.freeze([
    Object.freeze({ host: 'www.toutiao.com', path: /^\/(?:article|item)\/[1-9]\d*\/?$/ }),
    Object.freeze({ host: 'toutiao.com', path: /^\/(?:article|item)\/[1-9]\d*\/?$/ })
  ]),
  baijiahao: Object.freeze([
    Object.freeze({ host: 'baijiahao.baidu.com', path: /^\/s\/?$/, query: url => /^[1-9]\d*$/.test(url.searchParams.get('id') || '') })
  ]),
  sohu: Object.freeze([
    Object.freeze({ host: 'www.sohu.com', path: /^\/a\/[1-9]\d*_[1-9]\d*\/?$/ }),
    Object.freeze({ host: 'sohu.com', path: /^\/a\/[1-9]\d*_[1-9]\d*\/?$/ })
  ]),
  xiaohongshu: Object.freeze([
    Object.freeze({ host: 'www.xiaohongshu.com', path: /^\/(?:explore|discovery\/item)\/[0-9A-Fa-f]{24}\/?$/ }),
    Object.freeze({ host: 'xiaohongshu.com', path: /^\/(?:explore|discovery\/item)\/[0-9A-Fa-f]{24}\/?$/ })
  ]),
  douyin: Object.freeze([
    Object.freeze({ host: 'www.douyin.com', path: /^\/(?:video|note)\/[1-9]\d*\/?$/ }),
    Object.freeze({ host: 'douyin.com', path: /^\/(?:video|note)\/[1-9]\d*\/?$/ })
  ]),
  kuaishou: Object.freeze([
    Object.freeze({ host: 'www.kuaishou.com', path: /^\/short-video\/[A-Za-z0-9_-]{6,128}\/?$/ }),
    Object.freeze({ host: 'kuaishou.com', path: /^\/short-video\/[A-Za-z0-9_-]{6,128}\/?$/ })
  ]),
  bilibili: Object.freeze([
    Object.freeze({ host: 'www.bilibili.com', path: /^\/video\/(?:BV[A-Za-z0-9]{10}|av[1-9]\d*)\/?$/i }),
    Object.freeze({ host: 'bilibili.com', path: /^\/video\/(?:BV[A-Za-z0-9]{10}|av[1-9]\d*)\/?$/i })
  ]),
  tiktok: Object.freeze([
    Object.freeze({
      host: 'www.tiktok.com',
      path: /^\/@[A-Za-z0-9_](?:[A-Za-z0-9._]{0,22}[A-Za-z0-9_])?\/video\/[1-9]\d*\/?$/
    })
  ])
})

const normalizePlatform = platform => String(platform || '').trim().toLowerCase()

const PLATFORMS_WITHOUT_STABLE_PUBLIC_WORK_URL = new Set(['channels'])

const parseStrictHttpsUrl = value => {
  const raw = String(value || '').trim()
  if (!raw || raw.length > 2048 || /[\u0000-\u001f\\]/.test(raw)) return null
  try {
    const url = new URL(raw)
    if (
      url.protocol !== 'https:'
      || url.username
      || url.password
      || url.port
    ) return null
    return url
  } catch {
    return null
  }
}

export const platformVerificationDestination = platform => {
  const key = normalizePlatform(platform)
  const destination = VERIFICATION_DESTINATIONS[key]
  if (!destination) return null
  return {
    label: destination.label,
    url: destination.url
  }
}

export const isSafeExternalVerificationUrl = (value, platform) => {
  const key = normalizePlatform(platform)
  const destination = VERIFICATION_DESTINATIONS[key]
  const url = parseStrictHttpsUrl(value)
  if (!destination || !url) return false
  const allowed = new URL(destination.url)
  return url.hostname === allowed.hostname
    && (
      url.pathname === destination.pathPrefix
      || url.pathname.startsWith(`${destination.pathPrefix}/`)
    )
}

export const isSafePublishedContentUrl = (value, platform) => {
  const key = normalizePlatform(platform)
  const rules = PUBLIC_CONTENT_RULES[key]
  const url = parseStrictHttpsUrl(value)
  if (!rules || !url) return false
  return rules.some(rule => (
    url.hostname === rule.host
    && rule.path.test(url.pathname)
    && (!rule.query || rule.query(url))
  ))
}

export const isPublishedContentUrlRequired = platform => {
  const key = normalizePlatform(platform)
  return Boolean(key) && !PLATFORMS_WITHOUT_STABLE_PUBLIC_WORK_URL.has(key)
}

export const listPlatformVerificationDestinations = () => (
  Object.keys(VERIFICATION_DESTINATIONS).map(platform => ({
    platform,
    ...platformVerificationDestination(platform)
  }))
)
