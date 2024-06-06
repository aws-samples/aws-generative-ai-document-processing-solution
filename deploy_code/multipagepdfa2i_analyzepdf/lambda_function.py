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
import botocore
import os
import io
import datetime
import tempfile
from botocore.exceptions import ClientError

s3_client = boto3.client('s3')
ssm = boto3.client('ssm')
bedrock_runtime = boto3.client(
     service_name='bedrock-runtime', 
     region_name='us-east-1'
    )
sagemaker_a2i_runtime = boto3.client('sagemaker-a2i-runtime')
def invoke_to_get_back_to_stepfunction(event):
    client = boto3.client('stepfunctions')
    response = client.send_task_success(
        taskToken = event['token'],
        output = json.dumps({"all": "done"})
    )
    return response
def start_human_loop(human_loop_name, flow_definition_arn, input_content):
    """
    Start a human loop in Amazon SageMaker Ground Truth.
    Parameters:
    human_loop_name (str): The name of the human loop.
    flow_definition_arn (str): The ARN of the flow definition.
    input_content (str): The input content for the human loop as a JSON string.
    Returns:
    dict: Response from the start_human_loop API call.
    """
    # Start the human loop
    response = sagemaker_a2i_runtime.start_human_loop(
        HumanLoopName=human_loop_name,
        FlowDefinitionArn=flow_definition_arn,
        HumanLoopInput={
            'InputContent': input_content
        }
    )
    return response
def dump_task_token_in_dynamodb(event):
    dynamodb = boto3.client('dynamodb')
    response = dynamodb.put_item(
        TableName=os.environ['ddb_tablename'],
        Item={
            'jobid': {'S': event["human_loop_id"]},
            'callback_token': {'S': event["token"]}
        }
    )
    return response

def write_ai_response_to_bucket(event, data):
    client = boto3.client('s3')
    response = client.put_object(
        Body = json.dumps(data),
        Bucket = event["bucket"],
        Key = event["s3_location"]
    )
    return response

def download_image_from_s3(bucket, key):
    try:
        # Create a temporary file to store the downloaded image
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            # Download the image from S3 to the temporary file
            s3_client.download_file(bucket, key, tmp_file.name)

            # Read the image bytes from the temporary file
            with open(tmp_file.name, 'rb') as f:
                image_bytes = f.read()

        # Return the image bytes
        return image_bytes
    except ClientError as e:
        # Handle any errors that occur during the download
        print(f"Error downloading image from S3: {e}")
        return None
    finally:
        # Ensure the temporary file is deleted, even if an error occurs
        if os.path.exists(tmp_file.name):
            os.remove(tmp_file.name)


def call_claude_haiku(base64_string):
    prompt_config = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64_string,
                        },
                    },
                    {"type": "text", "text": "Answer the question as truthfully as possible strictly using only the provided image, and if the answer is not contained within the image or you are not sure about the value for any key provided, say \"I don't know\". Skip any preamble text and reasoning and give just the answer. Extract the following information  1)Name_of_applicant, 2)Day_phone_number, 3)Address, 4)City, 5)State, 6)Zip_code, 7)Email_address, 8) Your_relationship_to_person_named_on_this_certificate, 9)For_what_ purpose_are_you_requesting_this_certificate?, 10)Signature_of_applicant, 11)Name_on_birth_certificate_being_requested, 12)Date_of_birth, 13)Sex 14)City_of_birth, 15)County_of_birth, 16)Name_of_mother_parent_prior_to_1st_marriage, 17)Name_of_father_parent_prior_to_1st_marriage, 18)Mother_parent_state_or_foreign_country_of_birth, 19)Father_parent_state_or_foreign_country_of_birth, 20)Were_parents_married_at_the_time_of_birth?, 21)Number_of_children_born_to_this_individual, 22)Required_Search_Fee, 23)Each_Additional_copy, 24)Total_fees_submitted in Json format, Use the keys as provided in the prompt, respond back in the same order "},
                ],
            }
        ],
    }
    body = json.dumps(prompt_config)
    modelId = "anthropic.claude-3-haiku-20240307-v1:0"
    accept = "application/json"
    contentType = "application/json"
    response = bedrock_runtime.invoke_model(
        body=body, modelId=modelId, accept=accept, contentType=contentType
    )
    response_body = response.get("body").read().decode('utf-8')
    response_json = json.loads(response_body)
    #response_json = json.dumps(response_json)    
    results = response_json.get("content")[0].get("text")
    results.replace("/","_")
    return results
def parse_haiku_results(haiku_results):
    # Logic to parse haiku_results and extract key-value pairs
    # Assuming haiku_results is a JSON string, you can parse it to a dictionary
    kv_pairs = json.loads(haiku_results)
    return kv_pairs
    
def get_parameter(parameter_name):
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=False)
    return json.loads(response['Parameter']['Value'])
def validate_date_of_birth(date_of_birth, date_format):
    try:
        current_date = datetime.datetime.now()
        dob = datetime.datetime.strptime(date_of_birth, date_format)
        return dob < current_date
    except ValueError:
        return False

def validate_business_rules(kv_pairs):
    # Fetch required keys and values present parameter 
    if  ssm.get_parameter(Name='/business_rules/validationrequied', WithDecryption=False)['Parameter']['Value'] =='yes':
        required_keys_values_param = get_parameter('/business_rules/required_keys_values')
        # Ensure required_keys_values is a list
        if not isinstance(required_keys_values_param, list):
            raise ValueError("Parameter /business_rules/required_keys_values should be a JSON array.")
        # Validate if all required keys and values are present
        for key in required_keys_values_param:
            if key not in kv_pairs:
                print(f"Key '{key}' not found in kv_pairs")
                return False
            value = kv_pairs[key]
            if value is None or not value.strip():  # Check if value is None or empty after stripping
                print(f"Value for key '{key}' is empty")
                return False
        return True
    else:
        return True
    
def upload_output_to_s3(output_data, bucket, key):
    s3_client.put_object(Bucket=bucket, Key=key, Body=output_data)    
    
def encode_to_base64(image_bytes):
    import base64
    return base64.b64encode(image_bytes).decode('utf-8') 
    
def run_analyze_document(event):
    s3_client = boto3.client('s3')
    bedrock_runtime = boto3.client(service_name='bedrock-runtime',
                                   region_name='us-east-1')

    # Download the image from S3

    image_bytes = download_image_from_s3(event['bucket'],
            event['process_key'])
    # Encode image bytes to base64 string

    base64_string = encode_to_base64(image_bytes)

    # Call the Bedrock model
    # Construct the S3 path

    s3_path = os.path.join('s3://', event['bucket'], event['process_key'])
    haiku_results = call_claude_haiku(base64_string)
    # Parse the haiku_results to extract key-value pairs
    kv_pairs = parse_haiku_results(haiku_results)
    return haiku_results, s3_path, validate_business_rules(kv_pairs)
        
def lambda_handler(event, context):
    for record in event["Records"]:
        
        body = json.loads(record["body"])
        if body["wip_key"] == "single_image":
            body["process_key"] = "wip/" + body["id"] + "/" + body["wip_key"] + "/" + "0.png"
            body["human_loop_id"] = body["id"] + "i0"
            body["s3_location"] = "wip/" + body["id"] + "/0.png/ai/output.json"
        else:
            body["process_key"] = "wip/" + body["id"] + "/" + body["wip_key"] + ".png"
            body["human_loop_id"] = body["id"] + "i" + body["wip_key"]
            body["s3_location"] = body["process_key"] + "/ai/output.json"
            
        

        response, s3path, validate_business_rules = run_analyze_document(body)
        
        if validate_business_rules:
            data_dict = json.loads(response)
            write_ai_response_to_bucket(body, data_dict)
            response = invoke_to_get_back_to_stepfunction(body)
        else:
            # Set flag for human review if validation fails
            #need_to_human_review = True 
            # Start human loop if validation passes
            # Convert the string to a dictionary
            data_dict = json.loads(response)
            
            # Add the "s3path" key-value pair
            data_dict["taskObject"] = s3path
            
            # Convert the modified dictionary back to a string
            updated_data_str = json.dumps(data_dict, indent=4)
            
            write_ai_response_to_bucket(body, data_dict)
            response = start_human_loop(body["human_loop_id"], os.environ['human_workflow_arn'],updated_data_str)            
            responsetoken = dump_task_token_in_dynamodb(body)

        client = boto3.client('sqs')
        response = client.delete_message(
            QueueUrl=os.environ['sqs_url'],
            ReceiptHandle=record["receiptHandle"]
        )

    return "all_done"
