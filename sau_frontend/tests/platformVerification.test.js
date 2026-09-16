import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isSafeExternalVerificationUrl,
  isSafePublishedContentUrl,
  isPublishedContentUrlRequired,
  listPlatformVerificationDestinations,
  platformVerificationDestination
} from '../src/utils/platformVerification.js'

test('every publish platform has a direct creator content-management destination', () => {
  const destinations = listPlatformVerificationDestinations()
  assert.equal(destinations.length, 11)
  for (const item of destinations) {
    assert.match(item.url, /^https:\/\//)
    assert.equal(isSafeExternalVerificationUrl(item.url, item.platform), true)
  }
  assert.equal(
    platformVerificationDestination('tiktok')?.url,
    'https://www.tiktok.com/tiktokstudio'
  )
  assert.equal(
    platformVerificationDestination('weibo')?.url,
    'https://me.weibo.com/'
  )
})

test('management links are checked against the selected platform host and path', () => {
  assert.equal(isSafeExternalVerificationUrl('https://example.com/phishing', 'sohu'), false)
  assert.equal(isSafeExternalVerificationUrl('javascript:alert(1)', 'sohu'), false)
  assert.equal(
    isSafeExternalVerificationUrl('https://user:password@mp.sohu.com/mpfe/v4/contentManagement', 'sohu'),
    false
  )
  assert.equal(
    isSafeExternalVerificationUrl('https://mp.sohu.com:444/mpfe/v4/contentManagement', 'sohu'),
    false
  )
  assert.equal(
    isSafeExternalVerificationUrl('https://mp.sohu.com/not-content-management', 'sohu'),
    false
  )
  assert.equal(
    isSafeExternalVerificationUrl('https://mp.sohu.com/mpfe/v4/contentManagement', 'zhihu'),
    false
  )
  assert.equal(isSafeExternalVerificationUrl('https://me.weibo.com/', 'weibo'), true)
  assert.equal(isSafeExternalVerificationUrl('https://me.weibo.com/creator', 'weibo'), false)
  assert.equal(isSafeExternalVerificationUrl('https://me.weibo.com/?redirect=evil', 'weibo'), false)
  assert.equal(isSafeExternalVerificationUrl('https://weibo.com/', 'weibo'), false)
})

test('platform lookup is normalized and unknown platforms fail closed', () => {
  assert.equal(
    platformVerificationDestination(' SOHU ')?.url,
    'https://mp.sohu.com/mpfe/v4/contentManagement/first/page'
  )
  assert.equal(platformVerificationDestination('unknown'), null)
  assert.equal(isPublishedContentUrlRequired('sohu'), true)
  assert.equal(isPublishedContentUrlRequired('channels'), false)
})

test('public evidence URL allowlist accepts each platform content shape', () => {
  const samples = {
    zhihu: 'https://zhuanlan.zhihu.com/p/123456',
    toutiao: 'https://www.toutiao.com/article/123456/',
    baijiahao: 'https://baijiahao.baidu.com/s?id=123456',
    sohu: 'https://www.sohu.com/a/123456_100001',
    xiaohongshu: 'https://www.xiaohongshu.com/explore/0123456789abcdefABCDEF12',
    douyin: 'https://www.douyin.com/video/123456789',
    kuaishou: 'https://www.kuaishou.com/short-video/AbCd_123',
    bilibili: 'https://www.bilibili.com/video/BV1ab411c7dE/',
    tiktok: 'https://www.tiktok.com/@brand/video/123456789',
    weibo: 'https://weibo.com/7387181478/PzLkll9Yh'
  }

  for (const [platform, url] of Object.entries(samples)) {
    assert.equal(isSafePublishedContentUrl(url, platform), true, `${platform}: ${url}`)
  }
})

test('public evidence rejects management pages, cross-platform hosts and unsafe URLs', () => {
  for (const item of listPlatformVerificationDestinations()) {
    assert.equal(isSafePublishedContentUrl(item.url, item.platform), false, item.platform)
  }
  assert.equal(
    isSafePublishedContentUrl('https://www.zhihu.com/question/1/answer/2', 'sohu'),
    false
  )
  assert.equal(
    isSafePublishedContentUrl('http://www.sohu.com/a/123456_100001', 'sohu'),
    false
  )
  assert.equal(
    isSafePublishedContentUrl('https://user:secret@www.sohu.com/a/123456_100001', 'sohu'),
    false
  )
  assert.equal(
    isSafePublishedContentUrl('https://www.sohu.com:444/a/123456_100001', 'sohu'),
    false
  )
  assert.equal(
    isSafePublishedContentUrl('https://channels.weixin.qq.com/platform/post/list', 'channels'),
    false
  )
  assert.equal(
    isSafePublishedContentUrl('https://v.douyin.com/abc123/', 'douyin'),
    false
  )
  assert.equal(
    isSafePublishedContentUrl('https://b23.tv/abc123', 'bilibili'),
    false
  )
  assert.equal(
    isSafePublishedContentUrl('https://www.tiktok.com/@brand/photo/123456789', 'tiktok'),
    false
  )
  assert.equal(isSafePublishedContentUrl('https://m.weibo.cn/detail/5323991493381816', 'weibo'), true)
  assert.equal(isSafePublishedContentUrl('https://me.weibo.com/', 'weibo'), false)
  assert.equal(isSafePublishedContentUrl('https://t.cn/A6abc123', 'weibo'), false)
  assert.equal(isSafePublishedContentUrl('https://weibo.com/u/2892492275', 'weibo'), false)
  assert.equal(isSafePublishedContentUrl('https://weibo.com/7387181478', 'weibo'), false)
})
