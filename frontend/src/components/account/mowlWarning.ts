import { t } from '../../i18n/t'

/**
 * classbeat's local record of the playlists it added to this MOWL account goes with the
 * connection, whether it is disconnected or connected again in place, and with it any adding
 * that was left undecided (ADR 0006 section 3, #117 item 7).
 */
export const forgetWarning = (): string => t('account.forgetWarning')
