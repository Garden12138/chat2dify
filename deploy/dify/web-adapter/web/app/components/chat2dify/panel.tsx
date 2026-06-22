'use client'

import {
  Drawer,
  DrawerBackdrop,
  DrawerCloseButton,
  DrawerContent,
  DrawerPopup,
  DrawerPortal,
  DrawerTitle,
  DrawerViewport,
} from '@langgenius/dify-ui/drawer'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

type Chat2DifyIntent = 'create' | 'modify'

export type Chat2DifyPanelProps = {
  open: boolean
  intent: Chat2DifyIntent
  onOpenChange: (open: boolean) => void
  appId?: string
  appMode?: string
  appName?: string
}

const CHAT2DIFY_BASE_PATH = '/chat2dify/'

export const buildChat2DifyUrl = ({
  intent,
  appId,
  appMode,
  appName,
}: Pick<Chat2DifyPanelProps, 'intent' | 'appId' | 'appMode' | 'appName'>) => {
  const params = new URLSearchParams({
    embed: '1',
    intent,
  })

  if (appId)
    params.set('app_id', appId)
  if (appMode)
    params.set('app_mode', appMode)
  if (appName)
    params.set('app_name', appName)

  return `${CHAT2DIFY_BASE_PATH}?${params.toString()}`
}

const Chat2DifyPanel = ({
  open,
  intent,
  appId,
  appMode,
  appName,
  onOpenChange,
}: Chat2DifyPanelProps) => {
  const { t } = useTranslation()
  const title = intent === 'create' ? 'Chat2Dify 创建应用' : 'Chat2Dify 修改应用'
  const src = useMemo(() => buildChat2DifyUrl({
    intent,
    appId,
    appMode,
    appName,
  }), [intent, appId, appMode, appName])

  return (
    <Drawer open={open} modal swipeDirection="right" onOpenChange={onOpenChange}>
      <DrawerPortal>
        <DrawerBackdrop className="bg-black/20" />
        <DrawerViewport>
          <DrawerPopup className="justify-start bg-components-panel-bg! p-0! shadow-xl data-[swipe-direction=right]:top-3 data-[swipe-direction=right]:right-3 data-[swipe-direction=right]:bottom-3 data-[swipe-direction=right]:h-auto data-[swipe-direction=right]:w-[720px] data-[swipe-direction=right]:max-w-[calc(100vw-24px)] data-[swipe-direction=right]:rounded-2xl data-[swipe-direction=right]:border-[0.5px] data-[swipe-direction=right]:border-components-panel-border">
            <DrawerContent className="flex min-h-0 flex-1 flex-col overflow-hidden p-0 pb-0">
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-divider-subtle px-4">
                <DrawerTitle className="min-w-0 truncate system-md-semibold text-text-primary">
                  {title}
                </DrawerTitle>
                <DrawerCloseButton
                  aria-label={t('operation.close', { ns: 'common' })}
                  className="size-6 rounded-md"
                />
              </div>
              <iframe
                title={title}
                src={src}
                className="min-h-0 w-full flex-1 border-0 bg-background-body"
                allow="clipboard-read; clipboard-write"
              />
            </DrawerContent>
          </DrawerPopup>
        </DrawerViewport>
      </DrawerPortal>
    </Drawer>
  )
}

export default Chat2DifyPanel
