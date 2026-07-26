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
import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'
import {
  CHAT2DIFY_CONTEXT_PROTOCOL,
  createContextNonce,
  isChat2DifyFrameMessage,
} from './context-protocol'
import type { Chat2DifyCanvasContext, Chat2DifyContextMessageType } from './context-protocol'

type Chat2DifyIntent = 'create' | 'modify'

export type Chat2DifyPanelProps = {
  open: boolean
  intent: Chat2DifyIntent
  onOpenChange: (open: boolean) => void
  appId?: string
  appMode?: string
  appName?: string
  canvasContext?: Chat2DifyCanvasContext
}

const CHAT2DIFY_BASE_PATH = '/chat2dify/'

export const buildChat2DifyUrl = ({
  intent,
  appId,
  appMode,
  appName,
  contextNonce,
}: Pick<Chat2DifyPanelProps, 'intent' | 'appId' | 'appMode' | 'appName'> & {
  contextNonce?: string
}) => {
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
  if (contextNonce)
    params.set('context_nonce', contextNonce)

  return `${CHAT2DIFY_BASE_PATH}?${params.toString()}`
}

const Chat2DifyPanel = ({
  open,
  intent,
  appId,
  appMode,
  appName,
  canvasContext,
  onOpenChange,
}: Chat2DifyPanelProps) => {
  const { t } = useTranslation()
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const previousCanvasContextRef = useRef<Chat2DifyCanvasContext | undefined>(undefined)
  const [contextNonce, setContextNonce] = useState('')
  const title = intent === 'create' ? 'Chat2Dify 创建应用' : 'Chat2Dify 修改应用'
  useEffect(() => {
    if (open)
      setContextNonce(createContextNonce())
    else
      setContextNonce('')
  }, [open])
  const src = useMemo(() => buildChat2DifyUrl({
    intent,
    appId,
    appMode,
    appName,
    contextNonce,
  }), [intent, appId, appMode, appName, contextNonce])

  useEffect(() => {
    if (!open || !contextNonce)
      return
    const postContext = (type: Chat2DifyContextMessageType) => {
      if (!canvasContext || !iframeRef.current?.contentWindow)
        return
      iframeRef.current.contentWindow.postMessage({
        protocol: CHAT2DIFY_CONTEXT_PROTOCOL,
        type,
        context_nonce: contextNonce,
        payload: canvasContext,
      }, window.location.origin)
    }
    const handleMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin)
        return
      if (event.source !== iframeRef.current?.contentWindow)
        return
      if (!isChat2DifyFrameMessage(event.data, contextNonce))
        return
      postContext('dify.context.init')
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [open, contextNonce, canvasContext])

  useEffect(() => {
    if (!open || !contextNonce || !canvasContext || !iframeRef.current?.contentWindow)
      return
    const previous = previousCanvasContextRef.current
    const type = previous && (
      previous.dirty_state !== canvasContext.dirty_state
      || previous.canvas_draft_hash !== canvasContext.canvas_draft_hash
    )
      ? 'dify.draft.changed'
      : 'dify.selection.changed'
    iframeRef.current.contentWindow.postMessage({
      protocol: CHAT2DIFY_CONTEXT_PROTOCOL,
      type,
      context_nonce: contextNonce,
      payload: canvasContext,
    }, window.location.origin)
    previousCanvasContextRef.current = canvasContext
  }, [open, contextNonce, canvasContext])

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
                ref={iframeRef}
                title={title}
                src={contextNonce ? src : undefined}
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
