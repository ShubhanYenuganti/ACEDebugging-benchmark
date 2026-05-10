## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests through a message queue. When a request arrives, the system records it on the requester's side and creates a mirrored "pending" entry for the receiving player, allowing them to accept or reject. The read interface allows querying friendship status and listing all friends for a given player.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Friend requests are submitted successfully and the requester-side record is written correctly. However, the receiver-side "pending" entry is never created at the expected location — instead a malformed self-referential record appears at an unexpected key. Subsequent accept actions fail silently because no pending record exists at the right key. Additionally, any attempt to query a player's friend list via the read interface returns an error rather than results.

## What correct behavior looks like

After a Request is sent, both a "Requested" record for the requester and a "Pending" record for the receiver should be written with correctly mirrored player/friend key pairs. The read interface should return the list of friends for any player without error. After a complete Request → Accept cycle both sides should show "Friends."
