# /*
#  * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  * SPDX-License-Identifier: MIT-0
#  *
#  * Permission is hereby granted, free of charge, to any person obtaining a copy of this
#  * software and associated documentation files (the "Software"), to deal in the Software
#  * without restriction, including without limitation the rights to use, copy, modify,
#  * merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
#  * permit persons to whom the Software is furnished to do so.
#  *
#  * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
#  * INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
#  * PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
#  * HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
#  * OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
#  * SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#  */


import json
import boto3
import csv
import os
def lambda_handler(event, context):
    # Initialize S3 client
    s3 = boto3.client('s3')
    # Initialize DynamoDB client
    dynamodb = boto3.resource('dynamodb')
    for s3_item in event.get('s3path', []):
        # Get the S3 bucket and key from the event
        bucket = s3_item.get('bucket')
        key = s3_item.get('key')
        table = dynamodb.Table(os.environ['ddb_tablename'])
        # Download the CSV file from S3
        response = s3.get_object(Bucket=bucket, Key=key)
        lines = response['Body'].read().decode('utf-8').split('\n')
        # Parse CSV data and insert into DynamoDB table
        csv_reader = csv.DictReader(lines)
        has_human_keys = any(key.endswith('human') for key in csv_reader.fieldnames)
        for row in csv_reader:
            item = {}
            for key, value in row.items():
                if has_human_keys:
                    if key.endswith('human'):
                        item[key[:-6]] = value  # Remove 'human' from the column name
                else:
                    if key.endswith('ai'):
                        item[key[:-3]] = value  # Remove 'ai' from the column name
            table.put_item(Item=item)
    return {
        'statusCode': 200,
        'body': json.dumps('Data inserted into DynamoDB table successfully')
    }