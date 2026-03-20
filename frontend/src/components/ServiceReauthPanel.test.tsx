import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ServiceReauthPanel } from './ServiceReauthPanel'

describe('ServiceReauthPanel', () => {
  it('renders service context and allows actions', () => {
    const onAccept = vi.fn()
    const onDecline = vi.fn()

    render(
      <ServiceReauthPanel
        serviceDisplayName="Google Calendar"
        message="Google access expired."
        loading={false}
        onAccept={onAccept}
        onDecline={onDecline}
      />
    )

    expect(screen.getByText('Re-authorization required')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Re-authorize and continue' }))
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(onAccept).toHaveBeenCalledTimes(1)
    expect(onDecline).toHaveBeenCalledTimes(1)
  })
})
