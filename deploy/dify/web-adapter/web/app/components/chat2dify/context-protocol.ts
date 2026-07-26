export const CHAT2DIFY_CONTEXT_PROTOCOL = 'chat2dify.canvas-context.v1'
export const CHAT2DIFY_CONTEXT_VERSION = '1.0'

export type Chat2DifyCanvasContext = {
  protocol_version: typeof CHAT2DIFY_CONTEXT_VERSION
  revision: number
  selected_node_ids: string[]
  selected_edge_ids: string[]
  viewport: {
    x: number
    y: number
    zoom: number
  }
  current_panel?: string
  dirty_state: boolean
  canvas_draft_hash?: string
}

export type Chat2DifyContextMessageType
  = 'dify.context.init'
    | 'dify.selection.changed'
    | 'dify.draft.changed'

export type Chat2DifyHostMessage = {
  protocol: typeof CHAT2DIFY_CONTEXT_PROTOCOL
  type: Chat2DifyContextMessageType
  context_nonce: string
  payload: Chat2DifyCanvasContext
}

export type Chat2DifyFrameMessage = {
  protocol: typeof CHAT2DIFY_CONTEXT_PROTOCOL
  type: 'chat2dify.ready' | 'chat2dify.context.refresh'
  context_nonce: string
}

export const createContextNonce = () => {
  if (globalThis.crypto?.randomUUID)
    return globalThis.crypto.randomUUID()
  if (!globalThis.crypto?.getRandomValues)
    throw new Error('A cryptographically random context nonce is required.')
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(24))
  return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
}

export const isChat2DifyFrameMessage = (
  value: unknown,
  expectedNonce: string,
): value is Chat2DifyFrameMessage => {
  if (!value || typeof value !== 'object')
    return false
  const message = value as Record<string, unknown>
  return message.protocol === CHAT2DIFY_CONTEXT_PROTOCOL
    && (message.type === 'chat2dify.ready' || message.type === 'chat2dify.context.refresh')
    && message.context_nonce === expectedNonce
}
