import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import {
  CHAT2DIFY_CONTEXT_PROTOCOL,
} from '../context-protocol'
import Chat2DifyPanel, { buildChat2DifyUrl } from '../panel'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@langgenius/dify-ui/drawer', () => ({
  Drawer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DrawerBackdrop: () => <div />,
  DrawerCloseButton: (props: ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props} />,
  DrawerContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DrawerPopup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DrawerPortal: ({ children }: { children: ReactNode }) => <>{children}</>,
  DrawerTitle: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DrawerViewport: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

const nonce = 'safe-context-nonce-123456789'
const canvasContext = {
  protocol_version: '1.0' as const,
  revision: 1,
  selected_node_ids: ['llm-1'],
  selected_edge_ids: ['edge-1'],
  viewport: { x: 1, y: 2, zoom: 1 },
  current_panel: 'canvas',
  dirty_state: false,
  canvas_draft_hash: 'hash-v0',
}

describe('Chat2Dify secure canvas context panel', () => {
  beforeEach(() => {
    vi.stubGlobal('crypto', {
      randomUUID: () => nonce,
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('includes the per-panel nonce without putting graph data in the URL', () => {
    const url = buildChat2DifyUrl({
      intent: 'modify',
      appId: 'app-1',
      appMode: 'workflow',
      appName: 'Support',
      contextNonce: nonce,
    })

    expect(url).toContain(`context_nonce=${nonce}`)
    expect(url).not.toContain('graph')
    expect(url).not.toContain('llm-1')
  })

  it('accepts ready only from the iframe, exact origin, and exact nonce', async () => {
    render(
      <Chat2DifyPanel
        open
        intent="modify"
        appId="app-1"
        appMode="workflow"
        canvasContext={canvasContext}
        onOpenChange={vi.fn()}
      />,
    )
    const iframe = await screen.findByTitle('Chat2Dify 修改应用') as HTMLIFrameElement
    await waitFor(() => expect(iframe.src).toContain(`context_nonce=${nonce}`))
    const postMessage = vi.spyOn(iframe.contentWindow!, 'postMessage')
    postMessage.mockClear()

    fireEvent(window, new MessageEvent('message', {
      origin: 'https://evil.example',
      source: iframe.contentWindow,
      data: {
        protocol: CHAT2DIFY_CONTEXT_PROTOCOL,
        type: 'chat2dify.ready',
        context_nonce: nonce,
      },
    }))
    expect(postMessage).not.toHaveBeenCalled()

    fireEvent(window, new MessageEvent('message', {
      origin: window.location.origin,
      source: iframe.contentWindow,
      data: {
        protocol: CHAT2DIFY_CONTEXT_PROTOCOL,
        type: 'chat2dify.ready',
        context_nonce: 'stale-context-nonce-123456',
      },
    }))
    expect(postMessage).not.toHaveBeenCalled()

    fireEvent(window, new MessageEvent('message', {
      origin: window.location.origin,
      source: iframe.contentWindow,
      data: {
        protocol: CHAT2DIFY_CONTEXT_PROTOCOL,
        type: 'chat2dify.ready',
        context_nonce: nonce,
      },
    }))
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        protocol: CHAT2DIFY_CONTEXT_PROTOCOL,
        type: 'dify.context.init',
        context_nonce: nonce,
        payload: canvasContext,
      }),
      window.location.origin,
    )
  })
})
