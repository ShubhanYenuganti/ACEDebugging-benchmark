# Traffic Flow - Fuzzy Movie Search

## Architecture summary
Movie records are posted to an ingestion endpoint, streamed into a search index, and queried through a search endpoint used by a static website.

## Correct end-to-end flow

1. **DatasetLoader** -> **IngestFunctionUrl**
   Mechanism: HTTP proxy
   Required permission: lambda:InvokeFunctionUrl on ${IngestFunctionUrl}
   A loader posts one movie JSON document to the ingestion Lambda function URL.

2. **IngestFunctionUrl** -> **IngestFunction**
   Mechanism: event trigger
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${IngestFunction}
   Lambda receives the HTTP event body from the function URL.

3. **IngestFunction** -> **IngestStream**
   Mechanism: SDK call
   Required permission: kinesis:PutRecord on arn:aws:kinesis:${AWS::Region}:${AWS::AccountId}:stream/${IngestStream}
   The ingestion function writes the movie payload to the Kinesis stream.

4. **IngestStream** -> **IngestFirehoseStream**
   Mechanism: polling
   Required permission: kinesis:GetRecords on arn:aws:kinesis:${AWS::Region}:${AWS::AccountId}:stream/${IngestStream}
   Firehose reads records from the stream as its source.

5. **IngestFirehoseStream** -> **MovieSearchDomain**
   Mechanism: HTTP proxy
   Required permission: es:ESHttpPost on arn:aws:es:${AWS::Region}:${AWS::AccountId}:domain/${MovieSearchDomain}/*
   Firehose indexes each movie document into the `movies` index.

6. **IngestFirehoseStream** -> **SkippedDocumentsBucket**
   Mechanism: SDK call
   Required permission: s3:PutObject on arn:aws:s3:::${SkippedDocumentsBucket}/*
   Firehose writes failed or skipped records to the backup bucket.

7. **WebsiteBrowser** -> **WebsiteBucket**
   Mechanism: HTTP proxy
   Required permission: s3:GetObject on arn:aws:s3:::${WebsiteBucket}/*
   A browser loads the static search page and supporting assets.

8. **WebsiteBrowser** -> **SearchFunctionUrl**
   Mechanism: HTTP proxy
   Required permission: lambda:InvokeFunctionUrl on ${SearchFunctionUrl}
   The page calls the search function URL with query parameter `q`.

9. **SearchFunctionUrl** -> **SearchFunction**
   Mechanism: event trigger
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${SearchFunction}
   Lambda receives the search request event.

10. **SearchFunction** -> **MovieSearchDomain**
    Mechanism: HTTP proxy
    Required permission: es:ESHttpPost on arn:aws:es:${AWS::Region}:${AWS::AccountId}:domain/${MovieSearchDomain}/movies/_search
    The search function sends a fuzzy multi-match query to the movie index and returns matching documents.

## Integration dependencies

| From | To | Mechanism | Required permission |
|---|---|---|---|
| DatasetLoader | IngestFunctionUrl | HTTP proxy | lambda:InvokeFunctionUrl |
| IngestFunctionUrl | IngestFunction | event trigger | lambda:InvokeFunction |
| IngestFunction | IngestStream | SDK call | kinesis:PutRecord |
| IngestStream | IngestFirehoseStream | polling | kinesis:GetRecords |
| IngestFirehoseStream | MovieSearchDomain | HTTP proxy | es:ESHttpPost |
| IngestFirehoseStream | SkippedDocumentsBucket | SDK call | s3:PutObject |
| WebsiteBrowser | WebsiteBucket | HTTP proxy | s3:GetObject |
| WebsiteBrowser | SearchFunctionUrl | HTTP proxy | lambda:InvokeFunctionUrl |
| SearchFunctionUrl | SearchFunction | event trigger | lambda:InvokeFunction |
| SearchFunction | MovieSearchDomain | HTTP proxy | es:ESHttpPost |

## What breaks at each hop

**Hop 1 broken:** HTTP POST to the ingest URL returns 403/404/5xx and no Lambda invocation is recorded.

**Hop 2 broken:** The function URL responds with a Lambda service error and `IngestFunction` logs do not show the request.

**Hop 3 broken:** `IngestFunction` logs `PutRecord` errors and Kinesis incoming-record metrics remain flat.

**Hop 4 broken:** Kinesis records accumulate while Firehose delivery metrics remain flat.

**Hop 5 broken:** Firehose delivery errors increase and Elasticsearch `_search` returns zero hits for recently ingested titles.

**Hop 6 broken:** Firehose failed-record backup attempts return S3 access errors and skipped records are not present in the backup bucket.

**Hop 7 broken:** The website endpoint returns 403/404 or missing static assets from the website bucket.

**Hop 8 broken:** Browser search requests to the function URL return CORS or HTTP errors.

**Hop 9 broken:** Search requests fail before user code executes and Lambda invocation metrics remain flat.

**Hop 10 broken:** The search function logs Elasticsearch connection or query errors and returns no matching movie records.
