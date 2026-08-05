import { defineStore } from 'pinia'
import { ref } from 'vue'

// 放在 store 外，避免 Pinia 对普通对象状态处理异常导致页面读到空映射
export const PLATFORM_TYPES = {
  1: '小红书',
  2: '视频号',
  3: '抖音',
  4: '快手',
  5: '百家号',
  6: 'B站',
  7: '今日头条',
  8: '搜狐',
  9: '知乎'
}

export const useAccountStore = defineStore('account', () => {
  // 存储所有账号信息
  const accounts = ref([])

  // 设置账号列表
  const setAccounts = (accountsData) => {
    // 转换后端返回的数据格式为前端使用的格式
    accounts.value = accountsData.map(item => {
      return {
        id: item[0],
        type: item[1],
        filePath: item[2],
        name: item[3],
        status: item[4] === -1 ? '验证中' : (item[4] === 1 ? '正常' : '异常'),
        platform: PLATFORM_TYPES[item[1]] || '未知'
      }
    })
  }

  // 添加账号
  const addAccount = (account) => {
    accounts.value.push(account)
  }

  // 更新账号
  const updateAccount = (id, updatedAccount) => {
    const index = accounts.value.findIndex(acc => acc.id === id)
    if (index !== -1) {
      accounts.value[index] = { ...accounts.value[index], ...updatedAccount }
    }
  }

  // 删除账号
  const deleteAccount = (id) => {
    accounts.value = accounts.value.filter(acc => acc.id !== id)
  }

  // 根据平台获取账号
  const getAccountsByPlatform = (platform) => {
    return accounts.value.filter(acc => acc.platform === platform)
  }

  return {
    accounts,
    platformTypes: PLATFORM_TYPES,
    setAccounts,
    addAccount,
    updateAccount,
    deleteAccount,
    getAccountsByPlatform
  }
})
