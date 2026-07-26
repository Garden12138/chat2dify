'use client'

import {
  useEffect,
  useRef,
  useState,
} from 'react'
import { useStoreApi } from 'reactflow'
import { useWorkflowStore } from '@/app/components/workflow/store'
import type { Edge, Node } from '@/app/components/workflow/types'
import Chat2DifyPanel from './panel'
import type { Chat2DifyCanvasContext } from './context-protocol'

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
  const [canvasContext, setCanvasContext] = useState<Chat2DifyCanvasContext>()
  const workflowStore = useWorkflowStore()
  const reactFlowStore = useStoreApi()
  const revisionRef = useRef(0)
  const dirtyRef = useRef(false)
  const lastSyncedGraphRef = useRef('')

  useEffect(() => {
    if (!open)
      return
    const readContext = () => {
      const flow = reactFlowStore.getState()
      const workflow = workflowStore.getState()
      const nodes = flow.getNodes()
      const edges = flow.edges
      const graphSignature = canvasGraphSignature(nodes, edges, flow.transform)
      if (!lastSyncedGraphRef.current)
        lastSyncedGraphRef.current = graphSignature
      dirtyRef.current = workflow.isSyncingWorkflowDraft
        || graphSignature !== lastSyncedGraphRef.current
      revisionRef.current += 1
      const [x, y, zoom] = flow.transform
      setCanvasContext({
        protocol_version: '1.0',
        revision: revisionRef.current,
        selected_node_ids: nodes.filter(node => node.selected).map(node => node.id),
        selected_edge_ids: edges.filter(edge => edge.selected).map(edge => edge.id),
        viewport: { x, y, zoom },
        current_panel: currentWorkflowPanel(workflow),
        dirty_state: dirtyRef.current,
        canvas_draft_hash: workflow.syncWorkflowDraftHash || undefined,
      })
    }
    lastSyncedGraphRef.current = ''
    readContext()
    const unsubscribeFlow = reactFlowStore.subscribe(readContext)
    const unsubscribeWorkflow = workflowStore.subscribe((next, previous) => {
      if (
        next.syncWorkflowDraftHash !== previous.syncWorkflowDraftHash
        && !next.isSyncingWorkflowDraft
      ) {
        const flow = reactFlowStore.getState()
        lastSyncedGraphRef.current = canvasGraphSignature(
          flow.getNodes(),
          flow.edges,
          flow.transform,
        )
        dirtyRef.current = false
      }
      readContext()
    })
    return () => {
      unsubscribeFlow()
      unsubscribeWorkflow()
    }
  }, [open, reactFlowStore, workflowStore])

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
        canvasContext={canvasContext}
        onOpenChange={setOpen}
      />
    </>
  )
}

export default Chat2DifyWorkflowTrigger

const canvasGraphSignature = (
  nodes: Node[],
  edges: Edge[],
  transform: readonly number[],
) => JSON.stringify({
  nodes: nodes.map(node => ({
    id: node.id,
    position: node.position,
    data: Object.fromEntries(
      Object.entries(node.data || {}).filter(([key]) => !key.startsWith('_')),
    ),
  })),
  edges: edges.map(edge => ({
    id: edge.id,
    source: edge.source,
    sourceHandle: edge.sourceHandle,
    target: edge.target,
    targetHandle: edge.targetHandle,
    data: Object.fromEntries(
      Object.entries(edge.data || {}).filter(([key]) => !key.startsWith('_')),
    ),
  })),
  viewport: {
    x: transform[0],
    y: transform[1],
    zoom: transform[2],
  },
})

const currentWorkflowPanel = (state: Record<string, any>) => {
  const panels = [
    ['debug', state.showDebugAndPreviewPanel],
    ['features', state.showFeaturesPanel],
    ['variables', state.showChatVariablePanel || state.showGlobalVariablePanel || state.showEnvPanel],
    ['history', state.showWorkflowVersionHistoryPanel],
    ['comments', state.showCommentsPanel],
  ]
  return panels.find(([, visible]) => visible)?.[0] || 'canvas'
}
