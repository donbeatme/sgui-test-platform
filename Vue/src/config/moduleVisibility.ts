/** Presentation visibility only. Routes, permissions and stored data remain intact.
 * Remove a key from this set and rebuild to restore its entries.
 */
const hiddenModules = new Set([
  'testsuites', 'knowledge-management', 'api-testing', 'ui-automation',
  'task-center', 'file-management', 'api-keys',
]);

export const isModuleVisible = (key: string): boolean => !hiddenModules.has(key);
