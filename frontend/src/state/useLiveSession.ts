import { useEffect, useReducer } from "react";
import { eventsSocketUrl } from "../api/client";
import type { AgentEvent } from "../api/types";
import { initialLive, reduceEvent, type LiveSession } from "./liveSession";

type Action = { type: "event"; event: AgentEvent } | { type: "reset" };

function reducer(state: LiveSession, action: Action): LiveSession {
  return action.type === "reset" ? initialLive : reduceEvent(state, action.event);
}

/** Streams a session's events. The server replays history first, so this works mid-run too. */
export function useLiveSession(sessionId: string, enabled: boolean): LiveSession {
  const [state, dispatch] = useReducer(reducer, initialLive);

  useEffect(() => {
    dispatch({ type: "reset" });
  }, [sessionId]);

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      socket = new WebSocket(eventsSocketUrl(sessionId));
      socket.onmessage = (msg) => dispatch({ type: "event", event: JSON.parse(msg.data) as AgentEvent });
      socket.onclose = (ev) => {
        // 1000: stream ended normally. 1008: rejected (token). Otherwise reconnect; seq dedupes the replay.
        if (!closed && ev.code !== 1000 && ev.code !== 1008) retry = setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, [sessionId, enabled]);

  return state;
}
