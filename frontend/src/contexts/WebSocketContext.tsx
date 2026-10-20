// Origin: miStudio (Onegaishimas/miStudio) frontend/src/contexts/WebSocketContext.tsx @ c829a2cc
// Mode: copy (docs/REUSE.md), with these changes: subscriptions carry `room` (the backend's name,
// ADR-008) instead of `channel`; the socket factory is injectable so the reference counting can be
// tested without a server; console.log calls removed (lint allows warn and error only), typed
// handlers. Kept unchanged in substance: per-room reference counting (one component's unsubscribe
// once silenced every other subscriber), no re-attach on reconnect (MIS-E2E-120), pending queue
// cleared on unsubscribe (MIS-E2E-126), resubscribe on reconnect.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { io } from 'socket.io-client';
import type { Socket } from 'socket.io-client';

/** Socket.IO answers under the backend's /api/ prefix (backend/src/core/websocket.py). */
export const SOCKET_PATH = '/api/ws/socket.io';

type Handler = (...args: unknown[]) => void;

/** The part of a socket.io Socket the provider uses (so tests can pass a fake). */
export type SocketLike = Pick<Socket, 'on' | 'off' | 'emit' | 'disconnect'> & { connected: boolean };

export interface WebSocketContextValue {
  isConnected: boolean;
  subscribe: (room: string) => void;
  unsubscribe: (room: string) => void;
  on: (event: string, handler: Handler) => void;
  off: (event: string, handler?: Handler) => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

const defaultFactory = (): SocketLike =>
  io('', {
    path: SOCKET_PATH,
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    reconnectionAttempts: Infinity,
    transports: ['polling', 'websocket'],
  });

export function WebSocketProvider({
  children,
  createSocket = defaultFactory,
}: {
  children: ReactNode;
  createSocket?: () => SocketLike;
}) {
  const socketRef = useRef<SocketLike | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const subscriptionsRef = useRef<Set<string>>(new Set());
  // HOW MANY COMPONENTS WANT EACH ROOM. Membership belongs to the socket, not the component.
  const roomRefCountsRef = useRef<Map<string, number>>(new Map());
  const eventHandlersRef = useRef<Map<string, Set<Handler>>>(new Map());
  const pendingSubscriptionsRef = useRef<Set<string>>(new Set());
  const pendingHandlersRef = useRef<Array<{ event: string; handler: Handler }>>([]);

  useEffect(() => {
    const socket = createSocket();
    socketRef.current = socket;

    socket.on('connect', () => {
      setIsConnected(true);
      // No re-attach of handlers here: socket.io keeps listeners across reconnects (MIS-E2E-120).
      pendingHandlersRef.current.forEach(({ event, handler }) => {
        if (!eventHandlersRef.current.has(event)) eventHandlersRef.current.set(event, new Set());
        eventHandlersRef.current.get(event)!.add(handler);
        socket.on(event, handler);
      });
      pendingHandlersRef.current = [];
      subscriptionsRef.current.forEach((room) => socket.emit('subscribe', { room }));
      pendingSubscriptionsRef.current.forEach((room) => {
        subscriptionsRef.current.add(room);
        socket.emit('subscribe', { room });
      });
      pendingSubscriptionsRef.current.clear();
    });
    socket.on('disconnect', (reason: unknown) => {
      console.warn('[WebSocket] Disconnected:', reason, '— live updates stop; stores poll instead');
      setIsConnected(false);
    });
    socket.on('connect_error', (error: unknown) => {
      console.error('[WebSocket] Connection error:', error);
    });
    return () => {
      socket.disconnect();
      socketRef.current = null;
    };
  }, [createSocket]);

  const subscribe = useCallback((room: string) => {
    const claims = (roomRefCountsRef.current.get(room) ?? 0) + 1;
    roomRefCountsRef.current.set(room, claims);
    if (claims > 1) return;
    const socket = socketRef.current;
    if (!socket || !socket.connected) {
      pendingSubscriptionsRef.current.add(room);
      return;
    }
    subscriptionsRef.current.add(room);
    socket.emit('subscribe', { room });
  }, []);

  const unsubscribe = useCallback((room: string) => {
    const remaining = (roomRefCountsRef.current.get(room) ?? 0) - 1;
    if (remaining > 0) {
      // Someone else still listens; leaving the room would silence them.
      roomRefCountsRef.current.set(room, remaining);
      return;
    }
    roomRefCountsRef.current.delete(room);
    subscriptionsRef.current.delete(room);
    pendingSubscriptionsRef.current.delete(room);
    socketRef.current?.emit('unsubscribe', { room });
  }, []);

  const on = useCallback((event: string, handler: Handler) => {
    const socket = socketRef.current;
    if (!socket || !socket.connected) {
      pendingHandlersRef.current.push({ event, handler });
      return;
    }
    if (!eventHandlersRef.current.has(event)) eventHandlersRef.current.set(event, new Set());
    eventHandlersRef.current.get(event)!.add(handler);
    socket.on(event, handler);
  }, []);

  const off = useCallback((event: string, handler?: Handler) => {
    const socket = socketRef.current;
    if (handler) {
      eventHandlersRef.current.get(event)?.delete(handler);
      pendingHandlersRef.current = pendingHandlersRef.current.filter(
        (h) => !(h.event === event && h.handler === handler),
      );
      socket?.off(event, handler);
    } else {
      eventHandlersRef.current.delete(event);
      pendingHandlersRef.current = pendingHandlersRef.current.filter((h) => h.event !== event);
      socket?.off(event);
    }
  }, []);

  return (
    <WebSocketContext.Provider value={{ isConnected, subscribe, unsubscribe, on, off }}>
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocketContext(): WebSocketContextValue {
  const context = useContext(WebSocketContext);
  if (!context) throw new Error('useWebSocketContext must be used within WebSocketProvider');
  return context;
}

/** Like useWebSocketContext, but null outside a provider (tests, isolated components). */
export function useOptionalWebSocket(): WebSocketContextValue | null {
  return useContext(WebSocketContext);
}
