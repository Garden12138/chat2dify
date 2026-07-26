'use client'
import * as React from 'react'
import Chat2DifyConfigTrigger from '@/app/components/chat2dify/config-trigger'
import ConfigurationView from './configuration-view'
import { useConfiguration } from './hooks/use-configuration'

const Configuration = () => {
  const viewModel = useConfiguration()
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-divider-subtle bg-background-default-subtle px-6">
        <span className="system-xs-medium text-text-tertiary">
          Chat2Dify Builder Agent
        </span>
        <Chat2DifyConfigTrigger />
      </div>
      <div className="min-h-0 flex-1">
        <ConfigurationView {...viewModel} />
      </div>
    </div>
  )
}

export default React.memo(Configuration)
