// Feature 013 (ENH-004 §3.4, D2): every screen that lacked a Help link has one, and it opens Help at
// its own topic inside the REAL <App/> (the panel registry, the hash router and HelpLink). Removing
// a screen's `help=` prop, its topic, or the hash handling turns a case red.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { useHelpTopic } from '@/components/help/helpTopicStore';
import { HELP_TOPIC_IDS, helpTopic } from '@/config/help';

import { HEALTH, mockFetch } from './fetchMock';

/** screen (panel id) -> the Help topic its page head links to. */
const SCREENS: Array<[string, string]> = [
  ['version', 'version-detail'],
  ['calibration', 'calibration'],
  ['review', 'review'],
  ['generation', 'generation'],
  ['detector-sets', 'detector-sets'],
  ['publish', 'export'],
  ['settings', 'settings'],
  ['approvals', 'approvals'],
];

beforeEach(() => {
  useHelpTopic.setState({ topic: null });
  mockFetch({ 'GET /api/health': HEALTH, 'GET /api/v1/jobs': { jobs: [] }, 'GET /api/v1/approvals': { approvals: [] } });
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '#/');
});

describe('the seven screens without Help now link to it (ENH-004 §3.4)', () => {
  it.each(SCREENS)('%s links to the %s topic, which exists and names a manual page', async (panel, topic) => {
    expect(HELP_TOPIC_IDS).toContain(topic);
    expect(helpTopic(topic)?.manual).toBeTruthy();
    window.history.replaceState(null, '', `#/${panel}`);
    render(<App />);
    const link = await screen.findByTestId(`help-link-${topic}`);
    fireEvent.click(link);
    await waitFor(() => expect(screen.getByTestId('screen-help')).toBeInTheDocument());
    expect(document.getElementById(`help-${topic}`)).toHaveAttribute('data-current', 'true');
    expect(window.location.hash).toBe(`#/help/${topic}`);
  });
});

describe('a Help topic has its own address (ENH-004 D2)', () => {
  it('opening #/help/<topic> shows Help at that topic', async () => {
    window.history.replaceState(null, '', '#/help/label');
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('screen-help')).toBeInTheDocument());
    expect(document.getElementById('help-label')).toHaveAttribute('data-current', 'true');
    expect(window.location.hash).toBe('#/help/label');
  });

  it('a topic chip is a link to the topic, and following it makes that topic current', async () => {
    window.history.replaceState(null, '', '#/help');
    render(<App />);
    const chip = await screen.findByTestId('help-chip-words');
    expect(chip).toHaveAttribute('href', '#/help/words');
    act(() => {
      window.location.hash = '#/help/words';
    });
    await waitFor(() => expect(document.getElementById('help-words')).toHaveAttribute('data-current', 'true'));
  });

  it('"Words used here" links to the vocabulary in the manual', () => {
    expect(helpTopic('words')?.manual).toBe('intro#words-used-throughout');
  });
});
