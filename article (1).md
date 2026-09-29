# Community Emergency Coordination Agent

Emergency coordination is not primarily a language problem. It is a state-management problem: reports arrive with incomplete information, resources move, people change availability, and yesterday's decisions can matter when the next incident looks similar.

I built the Community Emergency Coordination Agent around that observation. The system takes citizen reports, turns them into structured incidents, verifies them against surrounding evidence, allocates resources and volunteers, sends notifications, and keeps the operational history in persistent memory. The part I found most interesting was not the multi-agent pipeline itself. It was deciding what the system should remember, what must remain exact, and when memory should influence a decision.

## What I built

The repository is organized around a Streamlit application plus a command-line path for running the coordination pipeline. At the center is an incident flow:

```text
Citizen report
     |
     v
Incident Agent
     |
     v
Verification Agent
     |
     +------> Resource Agent
     |
     +------> Volunteer Agent
     |
     v
Notification Agent
```

The agents share operational state, while Hindsight provides persistent semantic memory across incidents. The repository describes that memory loop as `recall / retain / reflect`: recall relevant history before making a decision, retain important outcomes after work is completed, and reflect when the system needs a higher-level answer about what has worked before.

I deliberately kept exact operational state outside semantic memory. Resource inventory belongs in structured state because “two boats available” is not a fuzzy fact. It is a number that has to be correct. The current implementation keeps that kind of state in JSON, while Hindsight stores incidents, resolutions, lessons, and other historical context.

That separation turned out to be one of the most important design decisions in the system.

## The real problem was context, not orchestration

It is easy to draw a diagram with five agents and call it a coordination system. The difficult part starts when the second, third, or twentieth incident arrives.

Consider a report such as:

> Water is entering homes in Kukatpally and several families are trapped.

The Incident Agent needs to extract the incident type, severity, location, and affected people. The Verification Agent needs to determine whether nearby reports or official alerts corroborate it. The Resource Agent needs to understand which assets are actually available. The Volunteer Agent needs to consider skills, distance, and previous task history.

None of those questions is answered well by looking only at the current message.

A recent flood in the same area may have already revealed which resources were useful. A volunteer who completed several water-rescue tasks may be a better match than someone who simply happens to be nearby. A previous incident may contain a resolution that is relevant even though the wording of the new report is completely different.

That is where I use Hindsight.

Instead of treating memory as a transcript archive, I treat it as an operational knowledge layer. Hindsight is designed around persistent agent memory rather than just conversation replay, with `retain`, `recall`, and `reflect` as its core operations. Its documentation describes recall as a combination of retrieval strategies and reflect as reasoning over recalled memories. That model fits emergency coordination surprisingly well.

I use [Hindsight for persistent agent memory](https://vectorize.io/what-is-agent-memory) because the useful historical signal is usually not “what did the user say?” It is “what did we learn when something similar happened?”

## Why I separated facts from experience

One of my strongest opinions in this project is that an AI agent should not be allowed to turn every piece of state into a vague memory.

Suppose the inventory contains:

```text
Boat-02: available
Pump-04: assigned
Ambulance-01: available
```

That information needs deterministic reads and writes. If an agent recalls that a boat was available last Tuesday, that does not mean the boat is available now.

So I use two different paths.

Structured state answers:

```text
What resources exist right now?
Who is currently assigned?
Which incidents are open?
Which volunteers are available?
```

Hindsight answers questions such as:

```text
What worked during similar floods?
Have we seen repeated reports in this area?
Which volunteer experience is relevant?
What lessons were recorded when this type of incident was resolved?
```

That boundary makes the system easier to reason about. Semantic memory can be incomplete without corrupting the live inventory.

The same principle appears in the LLM wrapper. The project keeps the language-model dependency behind a small interface, with a deterministic fallback when the model is unavailable:

```python
class LLM:
    # If no key is configured, available is False and agents
    # fall back to deterministic rules.
```

I like this pattern for operational software. The LLM is useful for interpretation and reasoning, but the entire application should not become unusable because an API key is missing or an external model is temporarily unavailable.

## Hindsight changed how I think about “memory”

The easiest implementation of agent memory is a vector store containing old messages. That is useful, but it does not fully answer the operational question.

Imagine the system has stored these incidents:

- A flood blocked a road near a school.
- Volunteers moved families using two boats.
- A pump cleared standing water after several hours.
- The same locality generated several reports during heavy rain.
- A volunteer repeatedly handled water-rescue tasks successfully.

A future query such as:

> What worked best for floods in Kukatpally?

should not require me to manually write a rule saying “look for boats and pumps.” I want the memory layer to find related experiences and help the agent reason over them.

That is the reason I use Hindsight's `reflect` capability in the design. Hindsight distinguishes `recall` from `reflect`: recall retrieves relevant memories, while reflect synthesizes a reasoned response using the memory context. The [Hindsight documentation](https://hindsight.vectorize.io/) describes these as separate operations rather than treating memory as a single similarity search.

The conceptual interface is small:

```python
# Store an outcome
client.retain(
    bank_id="community-emergency",
    content="Kukatpally flood resolved using two boats and a pump."
)

# Retrieve relevant experience
results = client.recall(
    bank_id="community-emergency",
    query="What worked for floods in Kukatpally?"
)

# Reason across the accumulated history
response = client.reflect(
    bank_id="community-emergency",
    query="What worked best for floods in Kukatpally?",
)
```

The important point is not the API syntax. It is the lifecycle.

An incident creates observations. Resolution creates experience. Experience becomes available to future incidents.

That gives the agent a way to improve its context without changing the application code every time a new operational pattern appears.

For reference, the open-source implementation and API details are available in the [Hindsight GitHub repository](https://github.com/vectorize-io/hindsight).

## The safety boundary matters more than the clever prompt

Emergency software has a different failure mode from an ordinary chatbot. A plausible answer is not necessarily a useful answer.

The repository therefore keeps the emergency chatbot deliberately cautious. Its system prompt explicitly tells the model to distinguish observations from uncertain guesses, avoid diagnosing injuries, avoid dangerous instructions, and direct people to official responders when there may be immediate danger.

The multimodal path follows the same principle:

```python
if image_bytes:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:{image_mime};base64,{encoded}"}
    })
```

The assistant can inspect an uploaded image when the configured model supports it, but the application does not treat a visual observation as proof of an incident or an exact location.

There is another important branch for sensitive reports. Certain safeguarding categories are handled locally and are intentionally kept outside the normal verification, memory, automated resource-dispatch, and notification path. That is a very different design from “send every user message through the same agent pipeline.”

I prefer explicit boundaries like this over relying on a single system prompt to protect the whole application.

## Verification should not become a bottleneck

The Verification Agent has an intentionally asymmetric rule: life-threatening reports should not be blocked simply because corroboration is missing.

That matters because verification and response have different objectives.

For a low-confidence, non-critical report, additional evidence can improve dispatch quality. For a potentially life-threatening report, waiting for perfect confidence can be worse than dispatching and flagging the case for human attention.

The architecture therefore treats verification as a signal rather than an absolute gate for every incident.

The same philosophy appears in the live-alert integration. Official IMD CAP/RSS alerts and citizen incidents are kept as separate sources, and an official alert in the same district can increase confidence in a citizen report. The system does not pretend that a citizen report and an official warning have the same evidentiary status.

That source separation is important for any system that combines machine-generated interpretation with external operational data.

## What an incident looks like end to end

Suppose someone reports:

> Water is entering homes in Kukatpally. Twelve families are trapped.

The first step is extraction. The Incident Agent identifies a flood-related incident, estimates severity, records the location, and captures the number of affected people.

The Verification Agent then looks for nearby corroboration. If an active official weather alert covers the same district, that becomes additional evidence. If similar citizen reports exist, they can contribute to the confidence picture.

Next, the Resource Agent looks at the actual current inventory. It does not ask semantic memory whether a boat exists; it checks structured state.

At the same time, the Volunteer Agent can use history to identify people whose previous completed tasks are relevant. This is one place where persistent memory is particularly useful: distance tells me who is nearby, while experience tells me who has handled similar work before.

Finally, the Notification Agent produces localized communication for the appropriate audience. The application supports English, Telugu, and Hindi notifications, while keeping the emergency contact and source information explicit.

When the incident is resolved, the system updates the operational state and stores the resolution as memory. A resolution such as:

```text
2 boats + pump cleared the affected area in 3 hours.
```

is more useful as future experience than as another row in an incident log.

The next incident can benefit from it.

## I also kept an offline path

One of the less glamorous choices in the repository is also one of the most practical.

If the Groq API is not configured, the LLM wrapper marks itself unavailable and the agents can fall back to deterministic rules. The chatbot similarly has a rule-based hazard detector.

For example:

```python
def detect_hazard(text):
    content = (text or "").casefold()

    for hazard, terms in HAZARD_TERMS.items():
        if any(term in content for term in terms):
            return hazard

    return "other"
```

That does not replace an LLM. It gives the system a predictable minimum behavior.

For an emergency coordination application, graceful degradation is not an optimization. It is part of the architecture.

## What I learned building it

### 1. Memory is useful only when it changes a future decision

I do not want a giant archive that the agent can technically search. I want historical experience to affect the next relevant incident.

That means retaining outcomes and lessons, not blindly retaining every event.

### 2. Semantic memory should not own exact state

Inventory, assignments, coordinates, incident status, and availability need authoritative structured storage. Hindsight is valuable for context and experience, not as the source of truth for live counts.

### 3. Verification and response have different priorities

Confidence is useful, but it should not become a universal blocking condition. Critical reports need a path to human attention even when evidence is incomplete.

### 4. LLMs should sit behind explicit interfaces

The `LLM` wrapper makes the rest of the application less dependent on one provider. It also makes deterministic fallback possible. That is much easier to test than scattering model calls across every agent.

### 5. The best agent architecture is mostly boundaries

The interesting part of this system is not that there are several agents. It is that each agent has a constrained responsibility, live state has an authoritative home, sensitive reports have a separate path, and historical experience has a persistent memory layer.

That is the pattern I would reuse.

## Where I would take it next

The next engineering step is not adding more agents. It is replacing the remaining local integrations with production infrastructure while preserving the same boundaries.

The resource and volunteer state should move to a transactional datastore with proper concurrency control. Geocoding should use verified services rather than a bundled gazetteer. Notification delivery should use real provider integrations with delivery receipts and retry policies. Operational events should become auditable events rather than only application logs.

For Hindsight, I would keep expanding the distinction between raw incident history and reusable operational knowledge. Its memory-bank model is a good fit for separating community-level experience from other scopes, while tags and metadata can provide additional boundaries where required.

The broader lesson for me is simple: an emergency agent does not become useful because it can generate a better paragraph. It becomes useful when it can connect the current situation to reliable current state, relevant past experience, and clear operational boundaries.

That is why I built the Community Emergency Coordination Agent around memory as a first-class system component rather than treating it as an afterthought.
