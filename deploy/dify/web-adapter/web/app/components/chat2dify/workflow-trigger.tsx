'use client'

import { useState } from 'react'
import Chat2DifyPanel from './panel'

type Chat2DifyWorkflowTriggerProps = {
  appId: string
  appMode?: string
  appName?: string
}

const Chat2DifyWorkflowTrigger = ({
  appId,
  appMode,
  appName,
}: Chat2DifyWorkflowTriggerProps) => {
  const [open, setOpen] = useState(false)

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
        appId={appId}
        appMode={appMode}
        appName={appName}
        onOpenChange={setOpen}
      />
    </>
  )
}

export default Chat2DifyWorkflowTrigger
