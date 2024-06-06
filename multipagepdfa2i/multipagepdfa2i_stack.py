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
#  * SOFTWARE OR THE USE OR OTHER DEALINGS IN THE hSOFTWARE.
#  */

# -------------------------------------------------------------------------------------------
# -------------------------------------------------------------------------------------------
# -------------------------------------------------------------------------------------------
# ~ ENTER SAGEMAKER AUGMENTED AI WORKFLOW ARN HERE:
SAGEMAKER_WORKFLOW_AUGMENTED_AI_ARN_EV = "arn:aws:sagemaker:us-east-1:289562521240:flow-definition/brithreviewworkflow"

# -------------------------------------------------------------------------------------------
# ---cdk----------------------------------------------------------------------------------------
# -------------------------------------------------------------------------------------------

import aws_cdk as cdk

from aws_cdk import (
    Stack,
    aws_s3,
    aws_lambda,
    aws_iam,
    aws_s3_notifications,
    aws_dynamodb,
    aws_stepfunctions,
    aws_stepfunctions_tasks,
    aws_sqs,
    aws_lambda_event_sources,
    aws_events,
    aws_ssm,
    aws_events_targets,
    aws_logs,  
    Aspects,
)
from constructs import Construct
from cdk_nag import NagSuppressions 


class Multipagepdfa2IStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        services = self.create_services()
        self.create_events(services)




    def create_state_machine(self, services):
        task_pngextract = aws_stepfunctions_tasks.LambdaInvoke(
            self,
            "PDF. Convert to PNGs",
            lambda_function=services["lambda"]["pngextract"],
            payload_response_only=True,
            result_path="$.image_keys",
        )

        task_wrapup = aws_stepfunctions_tasks.LambdaInvoke(
            self, "Wrapup and Clean", lambda_function=services["lambda"]["wrapup"],result_selector={"s3path.$": "$.Payload"}
        )
    
        task_image_resize = aws_stepfunctions_tasks.LambdaInvoke(
            self, "Image Resize", lambda_function=services["lambda"]["imageresize"], result_path="$.Input",
        )

        task_ddb_insert = aws_stepfunctions_tasks.LambdaInvoke(
            self, "Insert to dynamodb", lambda_function=services["lambda"]["inserttodynamodb"], result_path="$.Input",
        )        

        iterate_sqs_to_bedrock = aws_stepfunctions_tasks.SqsSendMessage(
            self,
            "Perform Bedrock GenAI and A2I",
            queue=services["bedrock_sqs"],
            message_body=aws_stepfunctions.TaskInput.from_object(
                {
                    "token": aws_stepfunctions.JsonPath.task_token,
                    "id.$": "$.id",
                    "bucket.$": "$.bucket",
                    "key.$": "$.key",
                    "wip_key.$": "$.wip_key",
                }
            ),
            delay=None,
            # integration_pattern=aws_stepfunctions.ServiceIntegrationPattern.WAIT_FOR_TASK_TOKEN
            integration_pattern=aws_stepfunctions.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
        )

        process_map = aws_stepfunctions.Map(
            self,
            "Process_Map",
            items_path="$.image_keys",
            result_path="$.Input",
            parameters={
                "id.$": "$.id",
                "bucket.$": "$.bucket",
                "key.$": "$.key",
                "wip_key.$": "$$.Map.Item.Value",
            },
        ).iterator(iterate_sqs_to_bedrock)

        choice_pass = aws_stepfunctions.Pass(
            self,
            "Image. Passing.",
            result=aws_stepfunctions.Result.from_array(["single_image"]),
            result_path="$.image_keys",
        )

        pdf_or_image_choice = aws_stepfunctions.Choice(self, "PDF or Image?")
        pdf_or_image_choice.when(
            aws_stepfunctions.Condition.string_equals("$.extension", "pdf"),
            #task_pngextract.next(task_image_resize),
            task_pngextract,
        )
        pdf_or_image_choice.when(
            aws_stepfunctions.Condition.string_equals("$.extension", "png"), choice_pass
        )
        pdf_or_image_choice.when(
            aws_stepfunctions.Condition.string_equals("$.extension", "jpg"), choice_pass
        )

        aws_ssm.StringParameter(
                    self,
                    "birthcertificatekeyvalues",
                    parameter_name="/business_rules/required_keys_values",
                    string_value='["Name_of_applicant", "Day_phone_number", "Address", "City", "State", "Zip_code", "Email_address", "Your_relationship_to_person_named_on_this_certificate", "For_what_purpose_are_you_requesting_this_certificate?", "Signature_of_applicant", "Name_on_birth_certificate_being_requested", "Date_of_birth", "Sex", "City_of_birth", "County_of_birth", "Name_of_mother_parent_prior_to_1st_marriage", "Name_of_father_parent_prior_to_1st_marriage", "Mother_parent_state_or_foreign_country_of_birth", "Father_parent_state_or_foreign_country_of_birth", "Were_parents_married_at_the_time_of_birth?", "Number_of_children_born_to_this_individual", "Required_Search_Fee", "Each_Additional_copy", "Total_fees_submitted"]'
                )
        aws_ssm.StringParameter(
                    self,
                    "birthcertificatevalidationrequied",
                    parameter_name="/business_rules/validationrequied",
                    string_value="no"
                )                
        # Creates the Step Functions
        multipagepdfa2i_sf = aws_stepfunctions.StateMachine(
            scope=self,
            id="multipagepdfa2i_stepfunction",
            state_machine_name="multipagepdfa2i_stepfunction",
            role=services["sf_iam_roles"]["sfunctions"],
            definition=pdf_or_image_choice.afterwards()
            .next(task_image_resize)
            .next(process_map)
            .next(task_wrapup)
            .next(task_ddb_insert),
            tracing_enabled=True,
            logs=aws_stepfunctions.LogOptions(
                destination=services["sf_log_group"],
                level=aws_stepfunctions.LogLevel.ALL
            )
        )

        return multipagepdfa2i_sf
    
  
    def create_iam_role_for_lambdas(self, services):
        iam_roles = {}

        names = ["kickoff", "pngextract", "analyzepdf", "humancomplete", "wrapup","imageresize","inserttodynamodb"]

        for name in names:
            iam_roles[name] = aws_iam.Role(
                scope=self,
                id="multipagepdfa2i_lam_role_" + name,
                assumed_by=aws_iam.ServicePrincipal("lambda.amazonaws.com"),
            )

        # !!!!kick off lambda function
        # invokes another lambda function - client.invoke
        # lists all step functions, used to look for the state machine arn - list_state_machines
        # invokes a step function - start_execution
        # puts item into dynamodb - put_item
        iam_roles["kickoff"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services['sf_sqs'].queue_arn,services['bedrock_sqs'].queue_arn],
                actions=[
                    "sqs:DeleteMessage",
                    "sqs:ReceiveMessage",
                ],
            )
        )

        iam_roles["kickoff"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:states:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:stateMachine:multipagepdfa2i_stepfunction"], 
                actions=[
                    "states:StartExecution",
                ],
            )
        )        
        

        iam_roles["kickoff"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:/aws/lambda/multipagepdfa2i_kickoff:*"],   
                actions=[ 
                    "logs:CreateLogGroup",
                ],
            )
        ) 

        iam_roles["kickoff"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_kickoff:*"],   
                actions=[ 
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",                    
                ],
            )
        )         
        
        iam_roles["imageresize"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services["main_s3_bucket"].bucket_arn,  f"{services['main_s3_bucket'].bucket_arn}/*"],
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                ],
            )
        )

        iam_roles["imageresize"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:/aws/lambda/multipagepdfa2i_imageresize:*"],   
                actions=[ 
                    "logs:CreateLogGroup",
                ],
            )
        ) 

        iam_roles["imageresize"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_imageresize:*"],   
                actions=[ 
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",                    
                ],
            )
        )         
        
        #!!!! pngextract lambda function
        # s3 get object
        # s3 put object
        iam_roles["pngextract"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services["main_s3_bucket"].bucket_arn,  f"{services['main_s3_bucket'].bucket_arn}/*"],
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                ],
            )
        )

        iam_roles["pngextract"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:/aws/lambda/multipagepdfa2i_pngextract:*"],   
                actions=[ 
                    "logs:CreateLogGroup",
                ],
            )
        ) 

        iam_roles["pngextract"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_pngextract:*"],   
                actions=[ 
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",                    
                ],
            )
        ) 

        # !!!! analyzepdf lambda function
        # step functions - sendtask success
        # dynmodb - put item
        # s3 put object
        # s3 object
        # sqs delete meesage
        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:sagemaker:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:flow-definition/*"],
                actions=[
                    "sagemaker:StartHumanLoop",
                ],
            )
        )

        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:bedrock:{cdk.Stack.of(self).region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"],
                actions=[
                    "bedrock:InvokeModel",
                ],
            )
        )        
        
        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:dynamodb:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:table/{services['ddbtable_multia2ipdf_callback'].table_name}"],
                actions=[
                    "dynamodb:PutItem",
                ],
            )
        )        

        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services['sf_sqs'].queue_arn,services['bedrock_sqs'].queue_arn],
                actions=[
                    "sqs:DeleteMessage",
                    "sqs:ReceiveMessage", 
                    "sqs:ChangeMessageVisibility",
                    "sqs:GetQueueAttributes",
                    "sqs:GetQueueUrl",
                ],
            )
        )          
        
        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:states:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:stateMachine:multipagepdfa2i_stepfunction"], 
                actions=[
                    "states:SendTaskSuccess",
                ],
            )
        )          

        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:ssm:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:parameter/business_rules/required_keys_values", f"arn:aws:ssm:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:parameter/business_rules/validationrequied"], 
                actions=[
                    "ssm:GetParameter"
                ],
            )
        )        

        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_analyzepdf:*"],   
                actions=[ 
                    "logs:CreateLogGroup",
                ],
            )
        ) 

        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_analyzepdf:*"],   
                actions=[ 
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",                    
                ],
            )
        )  

        iam_roles["analyzepdf"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services["main_s3_bucket"].bucket_arn,  f"{services['main_s3_bucket'].bucket_arn}/*"],
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                ],
            )
        )         
        # !!!! humancomplete lambda function
        # step functions send task success
        # s3 put_object
        # s3 Object
        # dynamodb table query

        iam_roles["humancomplete"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:dynamodb:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:table/{services['ddbtable_multia2ipdf_callback'].table_name}"],
                actions=[
                    "dynamodb:Query",
                ],
            )
        )

        iam_roles["humancomplete"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:states:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:stateMachine:multipagepdfa2i_stepfunction"], 
                actions=[
                    "states:SendTaskSuccess",
                ],
            )
        )         

        iam_roles["humancomplete"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:/aws/lambda/multipagepdfa2i_humancomplete:*"],   
                actions=[ 
                    "logs:CreateLogGroup",
                ],
            )
        ) 

        iam_roles["humancomplete"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_humancomplete:*"],   
                actions=[ 
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",                    
                ],
            )
        )  

        iam_roles["humancomplete"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services["main_s3_bucket"].bucket_arn,  f"{services['main_s3_bucket'].bucket_arn}/*"],
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                ],
            )
        )         

        iam_roles["inserttodynamodb"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:dynamodb:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:table/{services['ddbtable_Vital_Birth_Data'].table_name}"],
                actions=[
                    "dynamodb:PutItem",
                ],
            )
        )

        iam_roles["inserttodynamodb"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services["main_s3_bucket"].bucket_arn,  f"{services['main_s3_bucket'].bucket_arn}/*"],
                actions=[
                    "s3:GetObject",
                ],
            )
        )         
        
        iam_roles["inserttodynamodb"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:/aws/lambda/multipagepdfa2i_inserttodynamodb:*"],   
                actions=[ 
                    "logs:CreateLogGroup",
                ],
            )
        ) 

        iam_roles["inserttodynamodb"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_inserttodynamodb:*"],   
                actions=[ 
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",                    
                ],
            )
        )         
        
        # !!!! wrapup lambda function
        # s3 put object
        # s3 list object v2
        # s3 delete object
        # dynamodb query
        iam_roles["wrapup"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:ssm:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:parameter/business_rules/required_keys_values", f"arn:aws:ssm:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:parameter/business_rules/validationrequied"], 
                actions=[
                    "ssm:GetParameter"
                ],
            )
        )
        
        iam_roles["wrapup"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services["main_s3_bucket"].bucket_arn,  f"{services['main_s3_bucket'].bucket_arn}/*"],
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:Object",
                    "s3:DeleteObject",
                    "s3:ListBucket",                    
                ],
            )
        )        

        iam_roles["wrapup"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:/aws/lambda/multipagepdfa2i_wrapup:*"],   
                actions=[ 
                    "logs:CreateLogGroup",
                ],
            )
        ) 

        iam_roles["wrapup"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/lambda/multipagepdfa2i_wrapup:*"],   
                actions=[ 
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",                    
                ],
            )
        )
        
        NagSuppressions.add_resource_suppressions(
            [
                iam_roles["wrapup"],
                iam_roles["inserttodynamodb"],
                iam_roles["kickoff"],
                iam_roles["imageresize"],
                iam_roles["humancomplete"],
                iam_roles["analyzepdf"],
                iam_roles["pngextract"]
            ],
            [
                {
                    'id': 'AwsSolutions-IAM5',
                    'reason': 'Suppressing errors due to default IAM policy',
                    'appliesTo': [
                        'Resource::arn:aws:logs:*:*:*',
                        'Resource::*'
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_wrapup:*',
                        'Resource::<multipagepdfa2i1BE8D0A8.Arn>/*',
                        'Resource::<multipagepdfa2i1BE8D0A8.Arn>/*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_humancomplete:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_humancomplete:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_humancomplete:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_humancomplete:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_humancomplete:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_kickoff:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_kickoff:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_inserttodynamodb:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_inserttodynamodb:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_imageresize:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_imageresize:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_wrapup:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_wrapup:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_analyzepdf:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_pngextract:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_pngextract:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_kickoff:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_kickoff:*',
                        'Resource::<multipagepdfa2iwrapupA9D980CE.Arn>:*',
                        'Resource::<multipagepdfa2ipngextractDD0BB0CE.Arn>:*',
                        'Resource::<multipagepdfa2iinserttodynamodb7337F9FC.Arn>:*',
                        'Resource::<multipagepdfa2iimageresize749DCA71.Arn>:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_inserttodynamodb:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_inserttodynamodb:*',
                        'Resource::<multipagepdfa2i1BE8D0A8.Arn>/*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_imageresize:*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:/aws/lambda/multipagepdfa2i_imageresize:*',
                        'Resource::<multipagepdfa2i1BE8D0A8.Arn>/*',
                        'Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/multipagepdfa2i_wrapup:*',
                        'Action::s3:*',
                        'Resource::arn:aws:logs:*:*:log-group:/aws/lambda/multipagepdfa2i_kickoff:*',
                        f'Resource::{services["main_s3_bucket"].bucket_arn}/*'
                    ]
                }
            ]
        )

        return iam_roles
        
    def create_iam_role_for_stepfunction(self, services):
        iam_roles = {}

        names = ["sfunctions"]

        for name in names:
            iam_roles[name] = aws_iam.Role(
                scope=self,
                id="multipagepdfa2i_lam_role_" + name,
                assumed_by=aws_iam.ServicePrincipal("states.amazonaws.com"),
            )

        # !!!!kick off lambda function
        # invokes another lambda function - client.invoke
        # lists all step functions, used to look for the state machine arn - list_state_machines
        # invokes a step function - start_execution
        # puts item into dynamodb - put_item
        iam_roles["sfunctions"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[services['bedrock_sqs'].queue_arn],
                actions=[
                    "sqs:SendMessage"
                ],
            )
        )

        iam_roles["sfunctions"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:lambda:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:function:multipagepdfa2i_imageresize",
                f"arn:aws:lambda:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:function:multipagepdfa2i_inserttodynamodb",
                f"arn:aws:lambda:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:function:multipagepdfa2i_pngextract",
                f"arn:aws:lambda:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:function:multipagepdfa2i_wrapup",
                f"arn:aws:lambda:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:function:multipagepdfa2i_humancomplete",
                f"arn:aws:lambda:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:function:multipagepdfa2i_analyzepdf",
                f"arn:aws:lambda:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:function:multipagepdfa2i_kickoff"], 
                actions=[
                    "lambda:InvokeFunction",
                ],
            )
        )        
        
        iam_roles["sfunctions"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=[f"arn:aws:logs:{cdk.Stack.of(self).region}:{cdk.Stack.of(self).account}:log-group:/aws/stepfunctions/multipagepdfa2i_stepfunction_logs:*"],   
                actions=[ 
                    "logs:CreateLogDelivery",
                    "logs:DeleteLogDelivery",   
                    "logs:DescribeLogGroups",
                    "logs:DescribeResourcePolicies",    
                    "logs:GetLogDelivery",
                    "logs:ListLogDeliveries",    
                    "logs:PutResourcePolicy",
                    "logs:UpdateLogDelivery",                        
                ],
            )
        ) 

        iam_roles["sfunctions"].add_to_policy(
            statement=aws_iam.PolicyStatement(
                resources=["*"],   
                actions=[ 
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",   
                    "xray:PutTelemetryRecords",
                    "xray:PutTraceSegments",    
                ],
            )
        )         
        
        NagSuppressions.add_resource_suppressions(
            [
                iam_roles["sfunctions"],
            ],
            [
                {
                    'id': 'W12',
                    'reason': 'This is created for a POC. Customer wwhile deploying this for production will restrict the resource for xray policy',
                }
            ]
        )

        return iam_roles


        
    def create_lambda_functions(self, services):
        lambda_functions = {}
        

    # Define a Lambda layer
        my_layer = aws_lambda.LayerVersion(
            self, "sharplayer",
            code=aws_lambda.Code.from_asset( "./deploy_code/multipagepdfa2i_imageresize/sharplayer.zip"),  # Path to your layer code directory
            compatible_runtimes=[aws_lambda.Runtime.NODEJS_20_X],  # Specify compatible runtimes
            description="sharplayer" 
        )        

        lambda_functions["pngextract"] = aws_lambda.Function(
            scope=self,
            id="multipagepdfa2i_pngextract",
            function_name="multipagepdfa2i_pngextract",
            code=aws_lambda.Code.from_asset(
                "./deploy_code/multipagepdfa2i_pngextract/multipagepdfa2i_pngextract.jar"
            ),
            handler="Lambda::handleRequest",
            runtime=aws_lambda.Runtime.JAVA_21,
            timeout=cdk.Duration.minutes(15),
            memory_size=3000,
            role=services["iam_roles"]["pngextract"],
        )
        

        lambda_functions["imageresize"] = aws_lambda.Function(
            scope=self,
            id="multipagepdfa2i_imageresize",
            function_name="multipagepdfa2i_imageresize",
            code=aws_lambda.Code.from_asset(
                "./deploy_code/multipagepdfa2i_imageresize/"
            ),
            handler="index.handler",
            runtime=aws_lambda.Runtime.NODEJS_20_X,
            timeout=cdk.Duration.minutes(15),
            layers=[my_layer],
            memory_size=3000,
            role=services["iam_roles"]["imageresize"],
        )        


        lambda_functions["analyzepdf"] = aws_lambda.Function(
            scope=self,
            id="multipagepdfa2i_analyzepdf",
            function_name="multipagepdfa2i_analyzepdf",
            code=aws_lambda.Code.from_asset(
                "./deploy_code/multipagepdfa2i_analyzepdf/"
            ),
            handler="lambda_function.lambda_handler",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(3),
            memory_size=3000,
            role=services["iam_roles"]["analyzepdf"],
            environment={
                "sqs_url": services["bedrock_sqs"].queue_url,
                "human_workflow_arn": SAGEMAKER_WORKFLOW_AUGMENTED_AI_ARN_EV,
                "ddb_tablename": services["ddbtable_multia2ipdf_callback"].table_name,
            },
        )

        lambda_functions["inserttodynamodb"] = aws_lambda.Function(
            scope=self,
            id="multipagepdfa2i_inserttodynamodb",
            function_name="multipagepdfa2i_inserttodynamodb",
            code=aws_lambda.Code.from_asset(
                "./deploy_code/multipagepdfa2i_inserttodynamodb/"
            ),
            handler="lambda_function.lambda_handler",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(3),
            memory_size=3000,
            role=services["iam_roles"]["inserttodynamodb"],
            environment={
                "ddb_tablename": services["ddbtable_Vital_Birth_Data"].table_name,
            },            
        )        

        names = ["humancomplete", "wrapup"]

        for name in names:
            lambda_functions[name] = aws_lambda.Function(
                scope=self,
                id="multipagepdfa2i_" + name,
                function_name="multipagepdfa2i_" + name,
                code=aws_lambda.Code.from_asset(
                    "./deploy_code/multipagepdfa2i_" + name + "/"
                ),
                handler="lambda_function.lambda_handler",
                runtime=aws_lambda.Runtime.PYTHON_3_12,
                timeout=cdk.Duration.minutes(15),
                memory_size=3000,
                role=services["iam_roles"][name],
                environment={
                    "ddb_tablename": services["ddbtable_multia2ipdf_callback"].table_name,
                },                  
            )


 
        NagSuppressions.add_resource_suppressions(
            [
                lambda_functions["wrapup"],
                lambda_functions["inserttodynamodb"],
                lambda_functions["imageresize"],
                lambda_functions["humancomplete"],
                lambda_functions["analyzepdf"],
                lambda_functions["pngextract"]
            ],
            [
                {
                    'id': 'W89',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will be deploying the lambda functions in the  VPC',
                },
                {
                    'id': 'W58',
                    'reason': 'Lambda functions has permission to write CloudWatch Logs',
                },
                {
                    'id': 'W92',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will  define ReservedConcurrentExecutions to reserve simultaneous execution',
                },                
            ]
        )    
        return lambda_functions
        
      

    def create_events(self, services):
        # kickoff_notification = aws_s3_notifications.LambdaDestination(services["lambda"]["kickoff"])
        extensions = [
            "pdf",
            "pDf",
            "pDF",
            "pdF",
            "PDF",
            "Pdf",
            "png",
            "pNg",
            "pNG",
            "pnG",
            "PNG",
            "Png",
            "jpg",
            "jPg",
            "jPG",
            "jpG",
            "JPG",
            "Jpg",
        ]
        for extension in extensions:
            services["main_s3_bucket"].add_event_notification(
                aws_s3.EventType.OBJECT_CREATED,
                aws_s3_notifications.SqsDestination(services["sf_sqs"]),
                aws_s3.NotificationKeyFilter(prefix="uploads/", suffix=extension),
            )

        services["lambda"]["kickoff"].add_event_source(
            aws_lambda_event_sources.SqsEventSource(services["sf_sqs"], batch_size=1)
        )

        services["lambda"]["analyzepdf"].add_event_source(
            aws_lambda_event_sources.SqsEventSource(
                services["bedrock_sqs"], batch_size=1
            )
        )

        human_complete_target = aws_events_targets.LambdaFunction(
            services["lambda"]["humancomplete"]
        )

        human_review_event_pattern = aws_events.EventPattern(
            source=["aws.sagemaker"],
            detail_type=["SageMaker A2I HumanLoop Status Change"],
        )

        aws_events.Rule(
            self,
            "multipadepdfa2i_HumanReviewComplete",
            event_pattern=human_review_event_pattern,
            targets=[human_complete_target],
        )

    def create_services(self):
        services = {}
        # S3 bucket
        services["main_s3_bucket"] = aws_s3.Bucket(
            self, "multipagepdfa2i", removal_policy=cdk.RemovalPolicy.DESTROY,  
            encryption=aws_s3.BucketEncryption.S3_MANAGED,
            access_control=aws_s3.BucketAccessControl.BUCKET_OWNER_FULL_CONTROL
        )
        
        '''
        # Define bucket policy
        bucket_policy = aws_iam.PolicyStatement(
            effect=aws_iam.Effect.ALLOW,
            actions=["s3:GetObject","s3:PutObject","s3:DeleteObject"],
            resources=[servicehs["main_s3_bucket"].arn_for_objects("*")],
            principals=[aws_iam.ServicePrincipal("lambda.amazonaws.com"), aws_iam.ServicePrincipal("sagemaker.amazonaws.com")]
        )
        bucket_policy.add_condition("SecureTransport", {
            "aws:SecureTransport": "true"
        })
        # Add bucket policy
        services["main_s3_bucket"].add_to_resource_policy(bucket_policy)
       
        
        def configure_dynamo_table(self, table_name, primary_key, sort_key):
            demo_table = aws_dynamodb.Table(
                self,
                table_name,
                table_name=table_name,
                partition_key=aws_dynamodb.Attribute(
                    name=primary_key, type=aws_dynamodb.AttributeType.STRING
                ),
                sort_key=aws_dynamodb.Attribute(
                    name=sort_key, type=aws_dynamodb.AttributeType.STRING
                ),
                billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
                point_in_time_recovery=True,  # Enable backup (Point-in-Time Recovery)
                removal_policy=cdk.RemovalPolicy.DESTROY,
            )
        '''
 
        services["ddbtable_multia2ipdf_callback"] = aws_dynamodb.Table(
                        self,  "ddbtable_multia2ipdf_callback",
                        partition_key=aws_dynamodb.Attribute(
                            name="jobid", type=aws_dynamodb.AttributeType.STRING
                        ),
                        sort_key=aws_dynamodb.Attribute(
                            name="callback_token", type=aws_dynamodb.AttributeType.STRING
                        ),
                        billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
                        point_in_time_recovery=True,  # Enable backup (Point-in-Time Recovery)
                        removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        services["ddbtable_Vital_Birth_Data"] = aws_dynamodb.Table(
                        self,  "ddbtable_Vital_Birth_Data",
                        partition_key=aws_dynamodb.Attribute(
                            name="Name_of_applicant", type=aws_dynamodb.AttributeType.STRING
                        ),
                        sort_key=aws_dynamodb.Attribute(
                            name="Zip_code", type=aws_dynamodb.AttributeType.STRING
                        ),
                        billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
                        point_in_time_recovery=True,  # Enable backup (Point-in-Time Recovery)
                        removal_policy=cdk.RemovalPolicy.DESTROY,
        )        
            
        #self.configure_dynamo_table("multia2ipdf_callback", "jobid", "callback_token")
        #self.configure_dynamo_table("Vital_Birth_Data", "Name_of_applicant", "Zip_code")

        services["sf_sqs"] = aws_sqs.Queue(
            self,
            "multipagepdfa2i_sf_sqs",
            queue_name="multipagepdfa2i_sf_sqs",
            visibility_timeout=cdk.Duration.minutes(5),
        )

        services["bedrock_sqs"] = aws_sqs.Queue(
            self,
            "multipagepdfa2i_bedrock_sqs",
            queue_name="multipagepdfa2i_bedrock_sqs",
            visibility_timeout=cdk.Duration.minutes(3),
        )
        
        # Create a log group for the Step Functions
        services["sf_log_group"] = aws_logs.LogGroup(
            self,
            "/aws/stepfunctions/multipagepdfa2i_stepfunction_logs",
            log_group_name="/aws/stepfunctions/multipagepdfa2i_stepfunction_logs",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            retention=aws_logs.RetentionDays.ONE_WEEK
        )

        services["iam_roles"] = self.create_iam_role_for_lambdas(services)
        services["lambda"] = self.create_lambda_functions(services)
        
        services["sf_iam_roles"] = self.create_iam_role_for_stepfunction(services)

        services["sf"] = self.create_state_machine(services)

        # need to creak kick off here so we can pass the state machine arn...
        services["lambda"]["kickoff"] = aws_lambda.Function(
            scope=self,
            id="multipagepdfa2i_kickoff",
            function_name="multipagepdfa2i_kickoff",
            code=aws_lambda.Code.from_asset("./deploy_code/multipagepdfa2i_kickoff/"),
            handler="lambda_function.lambda_handler",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(5),
            memory_size=3000,
            role=services["iam_roles"]["kickoff"],
            environment={
                "sqs_url": services["sf_sqs"].queue_url,
                "state_machine_arn": services["sf"].state_machine_arn,
            },
        )

        NagSuppressions.add_resource_suppressions(
            [
                services["lambda"]["kickoff"]
            ],
            [
                {
                    'id': 'W89',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will be deploying the lambda functions in the  VPC',
                },
                {
                    'id': 'W58',
                    'reason': 'Lambda functions has permission to write CloudWatch Logs',
                },
                {
                    'id': 'W92',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will  define ReservedConcurrentExecutions to reserve simultaneous execution',
                }, 
                {
                    'id': 'W48',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will enable encryption',
                },                  
            ]
        )  
      
        NagSuppressions.add_resource_suppressions(
            [
                services["sf_log_group"]
            ],
            [
                {
                    'id': 'W84',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will enable encryption',
                }
            ]
        )  

        NagSuppressions.add_resource_suppressions(
            [
                services["bedrock_sqs"],
                services["sf_sqs"]
            ],
            [
                {
                    'id': 'W48',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will enable encryption',
                }
            ]
        )           
        
        NagSuppressions.add_resource_suppressions(
            [
                services["ddbtable_multia2ipdf_callback"],
                services["ddbtable_Vital_Birth_Data"]
            ],
            [
                {
                    'id': 'W74',
                    'reason': 'This is created for a POC. Customer will be deploying this for production will enable encryption',
                }
            ]
        )    
        
        NagSuppressions.add_resource_suppressions(
            [
                services["main_s3_bucket"],
            ],
            [
                {
                    'id': 'W51',
                    'reason': 'This is created for a POC. Customer will be deploying this will create the bucket policy',
                },
                {
                    'id': 'W35',
                    'reason': 'This is created for a POC. Customer will be deploying this will have access logging configured',
                }                
            ]
        )           
        
        

        return services
        
        
