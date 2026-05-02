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
Orders are being accepted and customers receive immediate
acknowledgment, but the fulfillment records never appear in the
data store. The backlog appears to be growing — submitted orders
seem to be accumulating somewhere in the system rather than being
processed. No errors are surfaced to end users or visible in the
API responses.

## What correct behavior looks like
A purchase order submitted to the API should result in a
corresponding record appearing in the data store within a few
seconds of the acknowledgment being returned to the customer.
