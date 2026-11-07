import { AGENT_ACTIVITY, type FeatureCore } from "#lib/call/feature-port.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { PARTICIPATION_SIGNAL } from "./wire.ts";

interface SeatFrame {
    participant_id: string;
    is_host: boolean;
}

interface AgentJoinedFrame {
    agent_id: string;
    name: string;
    sponsor: string;
    sponsor_id: string;
}

interface AgentLeftFrame {
    agent_id: string;
}

const REFUSED_TEXT = "Only the person who brought the agent or the host can remove it.";

function tileKey(agentId: string) {
    return `agent-${agentId}`;
}

export function install(core: FeatureCore): Promise<void> {
    let seatId = "";
    let isHost = false;
    const shown = new Set<string>();

    function mayRemove(frame: AgentJoinedFrame) {
        return isHost || frame.sponsor_id === seatId;
    }

    function show(frame: AgentJoinedFrame) {
        shown.add(frame.agent_id);
        core.showAgentTile(
            {
                name: frame.name,
                activity: AGENT_ACTIVITY.PRESENT,
                sponsor: frame.sponsor,
                onRemove: mayRemove(frame)
                    ? () => {
                          core.send({ type: PARTICIPATION_SIGNAL.AGENT_REMOVE, agent_id: frame.agent_id });
                      }
                    : undefined,
            },
            tileKey(frame.agent_id),
        );
    }

    function hide(agentId: string) {
        shown.delete(agentId);
        core.showAgentTile(null, tileKey(agentId));
    }

    const seated = (frame: SeatFrame) => {
        seatId = frame.participant_id;
        isHost = frame.is_host;
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(PARTICIPATION_SIGNAL.AGENT_JOINED, show);
    core.on(PARTICIPATION_SIGNAL.AGENT_LEFT, (frame: AgentLeftFrame) => {
        hide(frame.agent_id);
    });
    core.on(PARTICIPATION_SIGNAL.AGENT_REFUSED, () => {
        core.showToast(REFUSED_TEXT, core.toastKind.LOCKED, null);
    });
    core.onCallEnded(() => {
        for (const agentId of [...shown]) hide(agentId);
    });
    return Promise.resolve();
}
