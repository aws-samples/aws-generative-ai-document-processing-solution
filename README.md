# Processing PDF Documents with a Human-in-the-Loop using Amazon Bedrock and Amazon A2I

Detailed documentation of this solution is published as blog and available at the following link:
https://aws.amazon.com/blogs/machine-learning/scalable-intelligent-document-processing-using-amazon-bedrock/

## Prerequisites

1. Node.js
2. Python
3. AWS Command Line Interface (AWS CLI)—for instructions, see [Installing the AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-install.html)

## Deployment

The following code deploys the reference implementation in your AWS account. The solution deploys different components, including an S3 bucket, Step Functions, an Amazon Simple Queue Service (Amazon SQS) queue, and AWS Lambda functions using the AWS Cloud Development Kit (AWS CDK), which is an open-source software development framework to model and provision your cloud application resources using familiar programming languages.

1. In an AWS Cloud9 terminal, clone the GitHub repo:
	```
	git clone https://github.com/aws-samples/aws-generative-ai-document-processing-solution
	```
2. Execute the following command to adjust file permissions and to create the sharp npm package:
	```
	mkdir -p ~/environment/sharplayer/nodejs && cd ~/environment/sharplayer/nodejs 
	npm init -y && npm install --arch=x64 --platform=linux sharp 
	cd .. && zip -r sharplayer.zip . 
	cp sharplayer.zip ~/environment/aws-generative-ai-document-processing-solution/deploy_code/multipagepdfa2i_imageresize/ 
	cd .. && rm -r sharplayer
	```	
3. Change to the repository directory:
	```
	cd aws-generative-ai-document-processing-solution
	```
4. Run the following command:
	```
	pip install -r requirements.txt
	```
The first time you deploy an AWS CDK app into an environment for a specific AWS account and Region combination, you must install a bootstrap stack. This stack includes various resources that the AWS CDK needs to complete its operations. For example, this stack includes an Amazon Simple Storage Service (Amazon S3) bucket that the AWS CDK uses to store templates and assets during its deployment processes.

5. To install the bootstrap stack, run the following command:
	```
	cdk bootstrap
	```
6. From the project's root directory, run the following command to deploy the stack:
	```
	cdk deploy
	```
7. Update the cross-origin resource sharing (CORS) for the S3 bucket:
   a. On the Amazon S3 console, choose Buckets in the navigation pane.
   b. Choose the name of the bucket that was created in the AWS CDK deployment step. It should have a name format like multipagepdfa2i-multipagepdf-xxxxxxxxx.
   c. Choose Permissions.
   d. In the Cross-origin resource sharing (CORS) section, choose Edit.
   e. In the CORS configuration editor text box, enter the following CORS configuration:

      ```
      [
         {
            "AllowedHeaders": [
               "Authorization"
            ],
            "AllowedMethods": [
               "GET",
               "HEAD"
            ],
            "AllowedOrigins": [
               "*"
            ],
            "ExposeHeaders": [
               "Access-Control-Allow-Origin"
            ]
         }
      ]
      ```
8. Create a private team: https://docs.aws.amazon.com/sagemaker/latest/dg/sms-workforce-management.html
9. Create a human review workflow: https://console.aws.amazon.com/a2i/home?region=us-east-1#/human-review-workflows
10. Open the file `/aws-generative-ai-document-processing-solution/multipagepdfa2i/multipagepdfa2i_stack.py`. Update line 23 with the ARN of the human review workflow.

    ```python
    SAGEMAKER_WORKFLOW_AUGMENTED_AI_ARN_EV = ""
    ```

11. Run `cdk deploy` to update the solution with the human review workflow ARN.

## Clean Up

1. First, you'll need to completely empty the S3 bucket that was created.
2. Finally, you'll need to run:
   ```
   cdk destroy
   ```

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.