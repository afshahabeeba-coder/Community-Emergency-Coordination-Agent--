# Why I Kept Inventory Counts Out of Hindsight

When I started designing a community emergency coordination agent, I
assumed the hard part would be getting several agents to cooperate. It
turned out the harder question was deciding what an agent should
remember---and, just as importantly, what it should never remember as a
fuzzy semantic fact.

I built the system around that boundary. Emergency history, lessons,
volunteer experience, and recurring incident patterns belong in
long-term memory. Exact operational state---such as whether there are
exactly two boats available---does not.

## What I built

The system takes an emergency report such as:

> Water is entering homes in Kukatpally. Twelve families are trapped.

From there, the workflow is deliberately explicit:

``` text
Citizen report
     |
     v
IncidentAgent
     |
     v
VerificationAgent
     |
     v
ResourceAgent
     |
     v
VolunteerAgent
     |
     v
NotificationAgent
```

Hindsight sits across that pipeline as the persistent memory layer. It
is used to recall similar incidents, retrieve historical patterns,
reason about what has worked before, and retain the outcome of completed
incidents.

The repository separates those concerns into small Python modules.
`orchestrator.py` wires the agents together, `llm.py` handles the
Groq-backed reasoning layer, `state.py` owns structured operational
state, and `memory/hindsight_memory.py` provides the memory abstraction.
The Streamlit application exposes the dashboard, while `main.py`
provides a simple command-line interface for reporting, resolving,
querying, checking status, and resetting state.

That separation matters because emergency coordination is not just a
text-generation problem. Some decisions are semantic, while others are
transactions.

## The useful distinction: memory versus state

The most important design decision I made was to stop treating every
piece of information as memory.

An LLM can reason about a statement like:

> The last major flood in this area was handled effectively by combining
> boats with a pump.

That is a useful experience. It can be recalled later even if the
wording of a new incident is completely different.

But this is different:

> There are exactly two boats available right now.

That is not an experience. It is current operational state.

If I put both facts into the same semantic memory system, I create an
uncomfortable failure mode. A system can retrieve something that is
approximately relevant while being wrong about the exact number that
matters operationally.

I therefore made the boundary explicit in `state.py`:

``` python
"""Structured operational state (live inventory, volunteers, incident registry).

Hindsight holds the *semantic/long-term* memory; this holds the *transactional*
numbers (e.g. exactly 2 boats left) that must never be fuzzy.
"""
```

The state layer persists structured JSON and owns things such as
resources, volunteers, incidents, locations, and alert history. It can
also calculate geographic distance using coordinates rather than asking
an LLM to infer proximity.

This sounds obvious once written down. It was not obvious when designing
the system.

The temptation with agent architectures is to put more and more context
into the memory layer because retrieval makes it easy to bring
information back later. I think that is the wrong abstraction for
operational systems. Memory should help an agent reason from experience;
it should not become the source of truth for mutable counters.

## Why Hindsight fits the historical side

For the semantic side of the system, I use Hindsight as a dedicated
memory service rather than building another retrieval layer inside the
application.

The [Hindsight GitHub
repository](https://github.com/vectorize-io/hindsight) exposes the same
basic model I needed here: retain information, recall relevant memories,
and reflect over accumulated history. Its documentation describes
`recall` as retrieval and `reflect` as a way to reason over remembered
information rather than simply returning matching records.

My integration wraps those operations behind one `Memory` class:

``` python
class Memory:
    def __init__(self):
        self.bank = config.HINDSIGHT_BANK_ID
        self.client = None
        self.backend = "local"

        if Hindsight:
            try:
                kw = {"base_url": config.HINDSIGHT_BASE_URL}
                if config.HINDSIGHT_API_KEY:
                    kw["api_key"] = config.HINDSIGHT_API_KEY

                self.client = Hindsight(**kw)
                self.client.recall(bank_id=self.bank, query="ping")
                self.backend = "hindsight"
            except Exception:
                self.client = None
```

I like this arrangement because the application does not need to know
whether the memory backend is remote or local. The configuration selects
a Hindsight endpoint and bank, and the wrapper exposes the operations
the agents actually need.

The project config uses a dedicated bank:

``` text
HINDSIGHT_BASE_URL=http://localhost:8888
HINDSIGHT_API_KEY=
HINDSIGHT_BANK_ID=community-emergency
```

That gives the emergency coordinator its own memory scope instead of
mixing its history with unrelated agent workloads.

For teams evaluating agent memory, the [Vectorize guide to agent
memory](https://vectorize.io/what-is-agent-memory) is useful background
on why persistent memory is different from simply replaying a
conversation window.

## Retain, recall, reflect

I use the three Hindsight operations for different reasons.

`retain()` records durable experience. When an incident is resolved, the
resolution can become a future lesson:

``` python
def retain(self, content: str, context: str = "emergency-coordination") -> None:
    stamp = dt.datetime.utcnow()

    if self.client:
        try:
            self.client.retain(
                bank_id=self.bank,
                content=content,
                context=context,
                timestamp=stamp,
            )
            return
        except Exception:
            pass
```

`recall()` is useful when an agent needs concrete historical context.
For example, the verification stage can look for similar incidents or
historical patterns in an area.

`reflect()` is where the design gets more interesting. Instead of asking
for matching records, I can ask a question such as:

``` text
What worked best for floods in Kukatpally?
```

The memory layer sends that question to Hindsight:

``` python
def reflect(self, question: str) -> str:
    if self.client:
        try:
            res = self.client.reflect(
                bank_id=self.bank,
                query=question,
            )
            return getattr(res, "text", str(res))
        except Exception:
            pass

    hits = self.recall(question, k=6)
    return "Relevant past records:
- " + "
- ".join(hits)
```

That distinction is important. A normal lookup answers, "What records
resemble this query?" Reflection answers a more operational question:
"Given what has happened before, what pattern can I extract?"

The [Hindsight documentation](https://hindsight.vectorize.io/) goes
deeper into these memory operations and the underlying system.

## Memory becomes useful when an incident closes

The most valuable memory is often created after the immediate problem is
over.

Suppose an incident begins with a citizen report about flooding. The
agents classify it, check nearby evidence, allocate resources, and
assign volunteers. Eventually an operator resolves the incident with
notes such as:

``` text
2 boats + pump cleared the affected homes in 3h.
```

The exact resource state is updated transactionally. Resources are
released when the incident closes. Volunteer track records are updated
from completed work.

Separately, the resolution becomes semantic history.

That gives me two different questions with two different sources of
truth.

If I ask:

> How many boats are available right now?

I want structured state.

If I ask:

> What combination of resources has worked well for flooding in this
> area?

I want memory.

This distinction also makes the system easier to reason about during
failures. A memory outage should not make the inventory count uncertain.
Conversely, a stale or incomplete memory should not prevent the system
from maintaining exact resource state.

## Keeping the agent pipeline explicit

The orchestration code is intentionally boring:

``` python
class Coordinator:
    def __init__(self):
        self.llm, self.memory = LLM(), Memory()

        a = (self.llm, self.memory)

        self.incident = IncidentAgent(*a)
        self.verify = VerificationAgent(*a)
        self.resource = ResourceAgent(*a)
        self.volunteer = VolunteerAgent(*a)
        self.notify = NotificationAgent(*a)
```

I prefer this to hiding everything behind a single autonomous loop.

Each agent has a bounded responsibility. The incident agent converts
free text into a structured incident. Verification looks for duplication
and corroboration. Resource selection works against actual resource
state and historical experience. Volunteer selection can use skills,
distance, and track record. Notification turns the resulting action into
a message for the appropriate audience.

The result is easier to inspect than a single prompt that says
"coordinate the emergency."

The system also has a deliberate safety boundary around severe
incidents. Life-threatening reports are not allowed to wait indefinitely
for normal verification; high-severity cases can be dispatched and
flagged for human attention. Sensitive safeguarding reports are handled
separately and bypass the normal AI verification, memory, automated
dispatch, and notification path.

Those constraints are not side features. They are part of the
architecture.

## A concrete interaction

The command-line interface makes the intended lifecycle easy to see:

``` bash
python main.py report "Water entering homes in Kukatpally, 12 families trapped"

python main.py resolve INC-0001   --notes "2 boats + pump cleared it in 3h"

python main.py ask "What worked best for floods in Kukatpally?"
```

The first command creates an incident from natural language. The second
closes it with an operational outcome. The third asks the memory layer
to reason over previous experience.

Over time, those commands describe a feedback loop:

``` text
incident
   |
   v
response
   |
   v
resolution
   |
   +------> exact state update
   |
   +------> Hindsight retain()
                 |
                 v
          future recall/reflect()
```

That is the part I care about most. The system is not merely storing a
transcript of previous conversations. It is creating a record of
operational experience that can influence later decisions without
becoming the authoritative store for mutable facts.

## The fallback is important too

I also did not want memory availability to determine whether the entire
application could start.

The memory wrapper probes the configured Hindsight service. If it cannot
connect, it falls back to a local JSON store with simple keyword
scoring. The application can therefore continue operating without
turning a memory-service outage into a complete application outage.

The fallback is not equivalent to Hindsight. It is deliberately smaller.
But the abstraction means the agents can continue calling `retain()`,
`recall()`, and `reflect()` without knowing which backend is active.

That is a useful property for development and for operational
resilience.

The same principle applies to the LLM layer. The repository can report
whether it is using Groq or the rules-based fallback, rather than
silently pretending both modes are identical.

## What I learned

### 1. Do not turn a database into a memory system

Semantic memory is good at relationships, history, experience, and
approximate relevance. It is a poor place to store facts whose
correctness depends on an exact current value.

If the question contains the word "exactly," I want to know why an LLM
is involved at all.

### 2. Memory should capture outcomes, not everything

I do not need to retain every intermediate agent message. The useful
information is what happened, what was tried, what worked, and what
changed.

That makes later reflection much more useful than dumping entire
conversations into a vector store.

### 3. Reflection is different from retrieval

Retrieval answers "what is similar?" Reflection can answer "what pattern
emerges?"

That distinction becomes valuable once the system has enough history to
contain multiple incidents with different outcomes.

### 4. Agent boundaries are still useful

Calling something a multi-agent system does not make architecture
optional. I still want clear responsibilities, explicit inputs and
outputs, and a coordinator that makes the workflow visible.

When something goes wrong, I want to know whether classification,
verification, allocation, volunteer selection, or notification was
responsible.

### 5. Safety boundaries should be architectural

Emergency software should not rely on a model instruction alone to
decide whether sensitive information enters memory or whether a severe
report waits for another stage.

The repository therefore separates sensitive reports, exact operational
state, semantic memory, and human escalation paths. Those boundaries are
much easier to test and audit than a giant prompt.

## Where I would take it next

The architecture leaves room for real operational integrations without
changing the core model. Notifications can move from the current
delivery abstraction to production SMS, WhatsApp, or other channels.
Geocoding can replace the current local gazetteer. Resource and
volunteer data can come from verified operational systems rather than
seed data.

The important part is that those additions do not require turning
Hindsight into a source of truth for everything.

I want the agent to remember that a particular response worked,
recognize when a new incident resembles an old one, and reason about
accumulated experience. I do not want it deciding that there are
probably two boats available because some old incident mentioned two
boats.

That boundary ended up being the most important engineering decision in
the system.

An emergency coordinator needs memory, but it also needs facts that are
not allowed to become memories.
