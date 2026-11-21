/* eslint-disable no-console */
// Build-only fixture for consoleStrip.test.ts: top-level calls a production build must drop
// (log, debug, info) and keep (warn, error). Never imported by the app.
console.log('MARK_LOG');
console.debug('MARK_DEBUG');
console.info('MARK_INFO');
console.warn('MARK_WARN');
console.error('MARK_ERROR');
