# INTERFACES

## Receives From
- Governance Chief
- Report Writer
- Risk Officer
- Economics
- Gov Analyst
- On-Chain Analyst
- Legal Analyst
- Competitive Intel
- Portfolio Manager
- Monitoring systems

## Sends To
- Governance Chief
- Report Writer
- All specialist agents

## Required Inputs
- Current case identifier or topic
- Retrieval request or context brief
- Scope of analysis

## Optional Inputs
- Time range
- Source priority
- Prior report references
- Named comparables

## Mandatory Outputs
- Context pack
- Relevant precedents
- Source bundle
- Open knowledge gaps
- Retrieval confidence
- Fallback status where applicable

## Output Format
Retrieval memo with:
1. Current case
2. Most relevant sources
3. Most relevant precedents
4. Contradictory context
5. Missing context
6. Confidence in retrieval
7. Fallback status

## Escalate When
- Source quality is poor
- Retrieval confidence is low
- No close precedent exists
- Important records conflict materially
- Key institutional memory is missing

## Reject Input When
- Scope is undefined
- Topic is too vague to retrieve against
- Request asks for final judgment instead of retrieval

## Fallback Behavior
If retrieval confidence is low or the source base is thin:
- explicitly mark the case as context-poor
- identify the minimum missing information blocking stronger retrieval
- provide the best available directional context without overstating quality
- escalate the weakness to Governance Chief and downstream agents