import json
import os
import urllib.parse
import urllib.request


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    query = params.get("q")
    if not query:
        return _response(400, {"error": "Mandatory query parameter q missing"})
    search_query = {
        "query": {
            "multi_match": {
                "fields": ["title", "directors", "actors"],
                "query": query,
                "fuzziness": "AUTO",
                "type": "best_fields",
            }
        }
    }
    endpoint = os.environ["ELASTICSEARCH_ENDPOINT"]
    index = os.environ["ELASTICSEARCH_INDEX"]
    url = f"http://{endpoint}/{urllib.parse.quote(index)}/_search"
    req = urllib.request.Request(
        url,
        data=json.dumps(search_query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    movies = []
    for hit in result.get("hits", {}).get("hits", []):
        movie = {
            "_search_id": hit.get("_id"),
            "_search_score": hit.get("_score"),
        }
        movie.update(hit.get("_source", {}))
        movies.append(movie)
    return _response(200, movies)
