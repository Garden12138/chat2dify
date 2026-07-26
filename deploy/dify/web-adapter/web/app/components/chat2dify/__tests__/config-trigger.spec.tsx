import { fireEvent, render, screen } from '@testing-library/react'
import type { App } from '@/types/app'
import { AppModeEnum } from '@/types/app'
import Chat2DifyConfigTrigger from '../config-trigger'

const mockState = vi.hoisted(() => ({
  appDetail: null as App | null,
}))

vi.mock('@/app/components/app/store', () => ({
  useStore: (selector: (state: typeof mockState) => unknown) => selector(mockState),
}))

vi.mock('../panel', () => ({
  default: ({
    open,
    intent,
    appId,
    appMode,
    appName,
  }: Record<string, string | boolean>) => (
    <div
      data-testid="chat2dify-config-panel"
      data-open={String(open)}
      data-intent={intent}
      data-app-id={appId}
      data-app-mode={appMode}
      data-app-name={appName}
    />
  ),
}))

describe('Chat2Dify configured-app trigger', () => {
  beforeEach(() => {
    mockState.appDetail = {
      id: 'app-1',
      name: 'Support assistant',
      mode: AppModeEnum.CHAT,
    } as App
  })

  it.each([
    AppModeEnum.CHAT,
    AppModeEnum.COMPLETION,
    AppModeEnum.AGENT_CHAT,
  ])('opens a modification panel for %s', (mode) => {
    mockState.appDetail = {
      ...mockState.appDetail!,
      mode,
    }
    render(<Chat2DifyConfigTrigger />)

    fireEvent.click(screen.getByRole('button', { name: 'Chat2Dify' }))

    const panel = screen.getByTestId('chat2dify-config-panel')
    expect(panel).toHaveAttribute('data-open', 'true')
    expect(panel).toHaveAttribute('data-intent', 'modify')
    expect(panel).toHaveAttribute('data-app-id', 'app-1')
    expect(panel).toHaveAttribute('data-app-mode', mode)
    expect(panel).toHaveAttribute('data-app-name', 'Support assistant')
  })

  it('does not render on graph app modes', () => {
    mockState.appDetail = {
      ...mockState.appDetail!,
      mode: AppModeEnum.WORKFLOW,
    }

    render(<Chat2DifyConfigTrigger />)

    expect(screen.queryByRole('button', { name: 'Chat2Dify' })).not.toBeInTheDocument()
  })
})
