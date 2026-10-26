import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { WhoBadge } from './WhoBadge';

describe('WhoBadge', () => {
  it('marks an agent with the icon and the word "agent"', () => {
    const { container } = render(<WhoBadge who="agent:dataworks-mcp" />);
    expect(screen.getByTestId('who-agent')).toHaveTextContent('agent');
    expect(screen.getByText('dataworks-mcp')).toBeInTheDocument();
    expect(container.querySelector('svg')).not.toBeNull();
  });

  it('shows an operator name as plain text, without the agent mark', () => {
    const { container } = render(<WhoBadge who="Sean" />);
    expect(screen.getByTestId('who-operator')).toHaveTextContent('Sean');
    expect(container.querySelector('svg')).toBeNull();
  });
});
