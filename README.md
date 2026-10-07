# SiltProof

SiltProof verifies proof-of-work for pre-monsoon drain desilting.

The product helps a ward engineer decide how much of a contractor's bill should be approved and how much should be held for review.

## MVP

- One ward
- One contractor bill
- 18 drain sections
- Approximately 120 dumper trips
- S3 evidence ingestion
- Textract slip extraction
- EXIF and perceptual-hash photo checks
- Bedrock photo analysis and evidence summaries
- Ten deterministic verification rules
- Claimed vs verified vs held payment
- Engineer approve/hold decision

## Architecture

- React + TypeScript + Vite
- Amazon S3
- AWS Lambda
- Amazon DynamoDB
- Amazon API Gateway
- Amazon Textract
- Amazon Bedrock
- Amazon Location Service
- AWS Amplify Hosting
- AWS SAM

## Repository Structure

- `frontend/` — ward engineer dashboard
- `backend/functions/ingest/` — S3 evidence ingestion
- `backend/functions/api/` — API and verification rules
- `scripts/` — seed/reset/data-generation scripts
- `data/simulated/` — simulated hackathon data
- `docs/` — architecture and submission assets
