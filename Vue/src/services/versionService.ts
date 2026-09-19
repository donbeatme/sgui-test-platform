/**
 * 版本管理服务
 * 提供版本号显示和更新检测功能
 */
import packageJson from '../../package.json'

export interface VersionInfo {
  current: string
  latest?: string
  hasUpdate: boolean
  releaseUrl?: string
  releaseNotes?: string
  checkTime?: Date
}

/**
 * 获取当前版本号
 */
export function getCurrentVersion(): string {
  return packageJson.version || '0.0.0'
}

/**
 * 比较版本号
 * @returns 当 v1 > v2 返回 1；v1 < v2 返回 -1；相等返回 0
 */
export function compareVersions(v1: string, v2: string): number {
  const parts1 = v1.replace(/^v/, '').split('.').map(Number)
  const parts2 = v2.replace(/^v/, '').split('.').map(Number)
  
  for (let i = 0; i < Math.max(parts1.length, parts2.length); i++) {
    const p1 = parts1[i] || 0
    const p2 = parts2[i] || 0
    if (p1 > p2) return 1
    if (p1 < p2) return -1
  }
  return 0
}

/** Local builds do not follow upstream releases. */
export async function checkLatestVersion(): Promise<VersionInfo> {
  return { current: getCurrentVersion(), hasUpdate: false }
}

/**
 * 格式化版本号显示
 */
export function formatVersion(version: string): string {
  if (!version || version === '0.0.0') {
    return 'dev'
  }
  return `v${version.replace(/^v/, '')}`
}
