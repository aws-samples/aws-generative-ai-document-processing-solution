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
from operator import itemgetter

ssm = boto3.client('ssm')

def get_parameter(parameter_name):
    response = ssm.get_parameter(Name=parameter_name, WithDecryption=False)
    return json.loads(response['Parameter']['Value'])
    
def does_exsist(bucket, key):
    s3 = boto3.resource('s3')
    try:
        s3.Object(bucket, key).load()
    except botocore.exceptions.ClientError as e:
        return False
    else:
        return True

def write_data_to_bucket(payload, name, csv):
    dest = "wip/" + payload["id"] + "/csv/" + name.replace(".png", ".csv")
    s3 = boto3.resource('s3')
    s3.Object(payload["bucket"], dest).put(Body=csv)
    return dest

def get_data_from_bucket(bucket, key):
    client = boto3.client('s3')
    response = client.get_object(
        Bucket=bucket,
        Key=key
    )
    return json.load(response["Body"])

def create_csv(kv_list, give_type):
    if isinstance(kv_list, str):
        try:
            kv_dict = json.loads(kv_list)
        except json.JSONDecodeError:
            return "Error: Invalid JSON string", ""
    elif isinstance(kv_list, dict):
        kv_dict = kv_list
    else:
        return "Error: Invalid input type", ""
    outputkey = ""
    outputvalue = ""
    key_to_be_added = get_parameter('/business_rules/required_keys_values')
    for key, value in kv_dict.items():
        if key in key_to_be_added:
            keytype = key.replace(",", "") + "-" + str(give_type)
            outputkey += keytype + ","
            outputvalue += str(value).replace(",", "") + ","
    return outputkey, outputvalue

def write_to_s3(csv, payload, original_uplolad_key,page_number):
    client = boto3.client('s3')
    #x = original_uplolad_key.split("-")
    parts = original_uplolad_key.split("-")
    response = client.put_object(
        Body = csv,
        Bucket = payload["bucket"],
        Key = "complete/" + parts[1]  + "-" + page_number  + "-" + "output.csv"
    )
    return payload["bucket"], "complete/" + parts[1] + "-" + page_number + "-" + "output.csv"
    
def curate_data(base_image_keys, payload):
    data = ""
    upload_responses = []
    for base_key in base_image_keys:
        page_number = base_key[base_key.rfind("/") + 1:]
        page_number = "page " + str(int(page_number[:page_number.find(".")]) + 1)
        datakey = ""
        datavalue = ""
        datakeyhuman = ""
        datavaluehuman = ""
        if does_exsist(payload["bucket"], base_key + "/ai/output.json"):
            temp_data = get_data_from_bucket(payload["bucket"], base_key + "/ai/output.json")
            datakey, datavalue = create_csv(temp_data, "ai")
        if does_exsist(payload["bucket"], base_key + "/human/output.json"):
            temp_data = get_data_from_bucket(payload["bucket"], base_key + "/human/output.json")
            datakeyhuman, datavaluehuman = create_csv(temp_data, "human")
        data = datakey + datakeyhuman + "\n" + datavalue + datavaluehuman
        bucket, key = write_to_s3(data, payload, payload["key"].replace("/", "-"), page_number)
        upload_responses.append({"bucket": bucket, "key": key})
    return data, upload_responses

def get_base_image_keys(bucket, keys):
    temp = []
    for key in keys:
        if "/human/output.json" in key:
            temp.append(key[:key.rfind("/human/output.json")])
        if "/ai/output.json" in key:
            temp.append(key[:key.rfind("/ai/output.json")])
    return list(dict.fromkeys(temp))

def get_all_possible_files(event):
    files = []
    payload = {}

    payload["bucket"] = event["bucket"]
    payload["id"] = event["id"]
    payload["key"] = event["key"]

    for item in event["image_keys"]:
        if item == "single_image":
            base_key = "wip/" + payload["id"] + "/0.png"
        else:
            base_key = "wip/" + payload["id"] + "/" + item + ".png"
        
        possible_ai_output_key = base_key + "/ai/output.json"
        possible_human_output_key = base_key + "/human/output.json"

        s3 = boto3.resource('s3')
        try:
            s3.Object(event["bucket"], possible_ai_output_key).load()
            files.append(possible_ai_output_key)
        except botocore.exceptions.ClientError as e:
            pass

        try:
            s3.Object(event["bucket"], possible_human_output_key).load()
            files.append(possible_human_output_key)
        except botocore.exceptions.ClientError as e:
            pass

    return files, payload

def gather_and_combine_data(event):
    
    keys, payload = get_all_possible_files(event)
    base_image_keys = get_base_image_keys(payload["bucket"], keys)
    base_image_keys.sort()
    data, s3outputpath = curate_data(base_image_keys, payload)
    return data, s3outputpath, payload

