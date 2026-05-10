## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests and acceptances through a message queue. Once both records reach the final "Friends" state, the relationship is queryable via a read endpoint that accepts a player identifier and returns all that player's current friendships.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

The friend request and accept flow completes successfully — both records reach the "Friends" state in the data store. However, the read endpoint returns a 404 when queried using the URL published in the stack outputs. If the correct endpoint path is discovered and called, a 403 is returned instead.

## What correct behavior looks like

After both records reach "Friends" state, the read endpoint must return a 200 response listing the friendship. The URL published as a stack output must point to a valid, deployed stage. Requests to that stage must be authorized to invoke the read function.
