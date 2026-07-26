'use client'

import { useState } from 'react'
import { useStore as useAppStore } from '@/app/components/app/store'
import { AppModeEnum } from '@/types/app'
import Chat2DifyPanel from './panel'

const CONFIG_APP_MODES = new Set<AppModeEnum>([
  AppModeEnum.CHAT,
  AppModeEnum.COMPLETION,
  AppModeEnum.AGENT_CHAT,
])

const Chat2DifyConfigTrigger = () => {
  const appDetail = useAppStore(state => state.appDetail)
  const [open, setOpen] = useState(false)

  if (!appDetail || !CONFIG_APP_MODES.has(appDetail.mode))
    return null

  return (
    <>
      <button
        type="button"
        className="flex h-8 shrink-0 cursor-pointer items-center gap-1 rounded-lg border-[0.5px] border-components-button-secondary-border bg-components-button-secondary-bg px-2 system-xs-medium text-text-secondary shadow-xs hover:bg-components-button-secondary-bg-hover"
        onClick={() => setOpen(true)}
      >
        <span aria-hidden className="i-ri-sparkling-line size-4" />
        <span>Chat2Dify</span>
      </button>
      <Chat2DifyPanel
        open={open}
        intent="modify"
        appId={appDetail.id}
        appMode={appDetail.mode}
        appName={appDetail.name}
        onOpenChange={setOpen}
      />
    </>
  )
}

export default Chat2DifyConfigTrigger
