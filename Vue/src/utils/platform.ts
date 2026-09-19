import { getPublicAssetUrl } from './assetUrl'

export const platformName = 'SGUI自动化测试'
export const platformIcon = getPublicAssetUrl('sgui-mark.svg')

export function loginDestination(target: unknown): string {
  return typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')
    && !target.includes('\\') && !/^\/(login|register)([/?#]|$)/.test(target)
    ? target : '/workbench'
}
