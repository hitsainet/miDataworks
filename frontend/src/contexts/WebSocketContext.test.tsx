import { act, render } from '@testing-library/react';
import { useEffect } from 'react';
import { describe, expect, it } from 'vitest';

import { WebSocketProvider, useWebSocketContext } from './WebSocketContext';
import type { SocketLike } from './WebSocketContext';

function fakeSocket() {
  const handlers = new Map<string, Set<(...a: unknown[]) => void>>();
  const emitted: Array<[string, unknown]> = [];
  const socket = {
    connected: false,
    on: (event: string, h: (...a: unknown[]) => void) => {
      if (!handlers.has(event)) handlers.set(event, new Set());
      handlers.get(event)!.add(h);
      return socket;
    },
    off: (event: string, h?: (...a: unknown[]) => void) => {
      if (h) handlers.get(event)?.delete(h);
      else handlers.delete(event);
      return socket;
    },
    emit: (event: string, data: unknown) => {
      emitted.push([event, data]);
      return socket;
    },
    disconnect: () => socket,
    fire: (event: string, data?: unknown) => handlers.get(event)?.forEach((h) => h(data)),
  };
  return { socket, emitted };
}

function Subscriber({ room, onEvent, id }: { room: string; onEvent: (id: string, d: unknown) => void; id: string }) {
  const { subscribe, unsubscribe, on, off } = useWebSocketContext();
  useEffect(() => {
    const h = (d: unknown) => onEvent(id, d);
    subscribe(room);
    on('job:progress', h);
    return () => {
      off('job:progress', h);
      unsubscribe(room);
    };
  }, [room, id, onEvent, subscribe, unsubscribe, on, off]);
  return null;
}

describe('WebSocketProvider reference counting', () => {
  it('keeps the room while a second subscriber remains', () => {
    const { socket, emitted } = fakeSocket();
    const received: string[] = [];
    const onEvent = (id: string) => received.push(id);
    const factory = () => socket as unknown as SocketLike;
    const view = (both: boolean) => (
      <WebSocketProvider createSocket={factory}>
        <Subscriber room="dataworks/selftest/job_1" id="a" onEvent={onEvent} />
        {both && <Subscriber room="dataworks/selftest/job_1" id="b" onEvent={onEvent} />}
      </WebSocketProvider>
    );
    const { rerender } = render(view(true));
    act(() => {
      socket.connected = true;
      socket.fire('connect');
    });
    expect(emitted.filter(([e]) => e === 'subscribe')).toEqual([['subscribe', { room: 'dataworks/selftest/job_1' }]]);

    rerender(view(false)); // subscriber b unmounts
    expect(emitted.some(([e]) => e === 'unsubscribe')).toBe(false);
    act(() => socket.fire('job:progress', { progress: 50 }));
    expect(received).toEqual(['a']);

    rerender(<WebSocketProvider createSocket={factory}>{null}</WebSocketProvider>);
    expect(emitted.filter(([e]) => e === 'unsubscribe')).toEqual([['unsubscribe', { room: 'dataworks/selftest/job_1' }]]);
  });
});
