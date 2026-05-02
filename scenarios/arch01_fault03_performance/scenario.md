# Scenario: Order Fulfillment Pipeline

## System overview
This system receives customer purchase orders through a web API,
acknowledges them immediately, and processes them asynchronously in
the background. Fulfilled orders are recorded in a central data store
used by downstream reporting and fulfillment workflows.

## What you have access to
A deployed instance of this system is running in your local
environment. The CloudFormation template (`faulted.yaml`) and all
supporting deployment files are available to you directly. Diagnostic
tools are available via MCP to probe the running system. The system
deployed successfully.

## Reported symptom
Order processing is intermittently failing during peak traffic
periods. Individual test orders appear to process correctly, but
during higher volume periods a significant portion of orders never
reach the data store. The engineering team reports the issue began
after the most recent deployment. There are no visible errors in the
API responses — customers receive acknowledgments regardless.

## What correct behavior looks like
All submitted orders, including under concurrent load, should result
in corresponding records appearing in the data store within a few
seconds of acknowledgment. Fulfillment should be consistent and not
dependent on order volume or timing.
