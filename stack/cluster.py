from troposphere import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    AWS_STACK_ID,
    AWS_STACK_NAME,
    autoscaling,
    Base64,
    cloudformation,
    elasticloadbalancing as elb,
    Equals,
    FindInMap,
    GetAtt,
    iam,
    If,
    Join,
    logs,
    Not,
    Output,
    Parameter,
    Ref,
)

from troposphere.ecs import (
    Cluster,
    ContainerDefinition,
    Environment,
    LoadBalancer,
    LogConfiguration,
    PortMapping,
    Service,
    TaskDefinition,
)

from awacs import ecr

from .template import template
from .vpc import (
    loadbalancer_a_subnet,
    loadbalancer_b_subnet,
    container_a_subnet,
    container_b_subnet,
)
from .assets import assets_bucket
from .environment_variables import environment_variables
from .repository import repository
from .certificates import application as application_certificate
from .security_groups import (
    load_balancer_security_group,
    container_security_group,
)


container_instance_type = Ref(template.add_parameter(Parameter(
    "ContainerInstanceType",
    Description="The container instance type",
    Type="String",
    Default="t2.micro",
    AllowedValues=["t2.micro", "t2.small", "t2.medium", "t2.large"]
)))


web_worker_port = Ref(template.add_parameter(Parameter(
    "WebWorkerPort",
    Description="Web worker container exposed port",
    Type="Number",
    Default="8000",
)))


web_worker_cpu = Ref(template.add_parameter(Parameter(
    "WebWorkerCPU",
    Description="Web worker CPU units",
    Type="Number",
    Default="512",
)))


web_worker_memory = Ref(template.add_parameter(Parameter(
    "WebWorkerMemory",
    Description="Web worker memory",
    Type="Number",
    Default="700",
)))


web_worker_desired_count = Ref(template.add_parameter(Parameter(
    "WebWorkerDesiredCount",
    Description="Web worker task instance count",
    Type="Number",
    Default="2",
)))


web_worker_protocol = Ref(template.add_parameter(Parameter(
    "WebWorkerProtocol",
    Description="Web worker instance protocol",
    Type="String",
    Default="HTTP",
    AllowedValues=["HTTP", "HTTPS"],
)))


web_worker_health_check = Ref(template.add_parameter(Parameter(
    "WebWorkerHealthCheck",
    Description="Web worker health check URL path, e.g., \"/health-check\"; "
                "will default to TCP-only health check if left blank",
    Type="String",
    Default="",
)))


max_container_instances = Ref(template.add_parameter(Parameter(
    "MaxScale",
    Description="Maximum container instances count",
    Type="Number",
    Default="3",
)))


desired_container_instances = Ref(template.add_parameter(Parameter(
    "DesiredScale",
    Description="Desired container instances count",
    Type="Number",
    Default="3",
)))


app_revision = Ref(template.add_parameter(Parameter(
    "WebAppRevision",
    Description="An optional docker app revision to deploy",
    Type="String",
    Default="",
)))


deploy_condition = "Deploy"
template.add_condition(deploy_condition, Not(Equals(app_revision, "")))


tcp_health_check_condition = "TcpHealthCheck"
template.add_condition(
    tcp_health_check_condition,
    Equals(web_worker_health_check, ""),
)


template.add_mapping("ECSRegionMap", {
    "us-east-1": {"AMI": "ami-eca289fb"},
    "us-east-2": {"AMI": "ami-446f3521"},
    "us-west-1": {"AMI": "ami-9fadf8ff"},
    "us-west-2": {"AMI": "ami-7abc111a"},
    "eu-west-1": {"AMI": "ami-a1491ad2"},
    "eu-central-1": {"AMI": "ami-54f5303b"},
    "ap-northeast-1": {"AMI": "ami-9cd57ffd"},
    "ap-southeast-1": {"AMI": "ami-a900a3ca"},
    "ap-southeast-2": {"AMI": "ami-5781be34"},
})


# Web load balancer

load_balancer = elb.LoadBalancer(
    'LoadBalancer',
    template=template,
    Subnets=[
        Ref(loadbalancer_a_subnet),
        Ref(loadbalancer_b_subnet),
    ],
    SecurityGroups=[Ref(load_balancer_security_group)],
    Listeners=[
        elb.Listener(
            LoadBalancerPort=80,
            InstanceProtocol=web_worker_protocol,
            InstancePort=web_worker_port,
            Protocol='HTTP',
        ),
        elb.Listener(
            LoadBalancerPort=443,
            InstanceProtocol=web_worker_protocol,
            InstancePort=web_worker_port,
            Protocol='HTTPS',
            SSLCertificateId=application_certificate,
        ),
    ],
    HealthCheck=elb.HealthCheck(
        Target=If(
            tcp_health_check_condition,
            Join("", ["TCP:", web_worker_port]),
            Join("", [
                web_worker_protocol,
                ":",
                web_worker_port,
                web_worker_health_check,
            ]),
        ),
        HealthyThreshold="2",
        UnhealthyThreshold="2",
        Interval="100",
        Timeout="10",
    ),
    CrossZone=True,
)

template.add_output(Output(
    "LoadBalancerDNSName",
    Description="Loadbalancer DNS",
    Value=GetAtt(load_balancer, "DNSName")
))


# ECS cluster
cluster = Cluster(
    "Cluster",
    template=template,
)


# ECS container role
container_instance_role = iam.Role(
    "ContainerInstanceRole",
    template=template,
    AssumeRolePolicyDocument=dict(Statement=[dict(
        Effect="Allow",
        Principal=dict(Service=["ec2.amazonaws.com"]),
        Action=["sts:AssumeRole"],
    )]),
    Path="/",
    Policies=[
        iam.Policy(
            PolicyName="AssetsManagementPolicy",
            PolicyDocument=dict(
                Statement=[dict(
                    Effect="Allow",
                    Action=[
                        "s3:ListBucket",
                    ],
                    Resource=Join("", [
                        "arn:aws:s3:::",
                        Ref(assets_bucket),
                    ]),
                ), dict(
                    Effect="Allow",
                    Action=[
                        "s3:*",
                    ],
                    Resource=Join("", [
                        "arn:aws:s3:::",
                        Ref(assets_bucket),
                        "/*",
                    ]),
                )],
            ),
        ),
        iam.Policy(
            PolicyName="ECSManagementPolicy",
            PolicyDocument=dict(
                Statement=[dict(
                    Effect="Allow",
                    Action=[
                        "ecs:*",
                        "elasticloadbalancing:*",
                    ],
                    Resource="*",
                )],
            ),
        ),
        iam.Policy(
            PolicyName='ECRManagementPolicy',
            PolicyDocument=dict(
                Statement=[dict(
                    Effect='Allow',
                    Action=[
                        ecr.GetAuthorizationToken,
                        ecr.GetDownloadUrlForLayer,
                        ecr.BatchGetImage,
                        ecr.BatchCheckLayerAvailability,
                    ],
                    Resource="*",
                )],
            ),
        ),
        iam.Policy(
            PolicyName="LoggingPolicy",
            PolicyDocument=dict(
                Statement=[dict(
                    Effect="Allow",
                    Action=[
                        "logs:Create*",
                        "logs:PutLogEvents",
                    ],
                    Resource="arn:aws:logs:*:*:*",
                )],
            ),
        ),
    ]
)


# ECS container instance profile
container_instance_profile = iam.InstanceProfile(
    "ContainerInstanceProfile",
    template=template,
    Path="/",
    Roles=[Ref(container_instance_role)],
)


container_instance_configuration_name = "ContainerLaunchConfiguration"


autoscaling_group_name = "AutoScalingGroup"


container_instance_configuration = autoscaling.LaunchConfiguration(
    container_instance_configuration_name,
    template=template,
    Metadata=autoscaling.Metadata(
        cloudformation.Init(dict(
            config=cloudformation.InitConfig(
                commands=dict(
                    register_cluster=dict(command=Join("", [
                        "#!/bin/bash\n",
                        # Register the cluster
                        "echo ECS_CLUSTER=",
                        Ref(cluster),
                        " >> /etc/ecs/ecs.config\n",
                        # Enable CloudWatch docker logging
                        'echo \'ECS_AVAILABLE_LOGGING_DRIVERS=',
                        '["json-file","awslogs"]\'',
                        " >> /etc/ecs/ecs.config\n",
                    ]))
                ),
                files=cloudformation.InitFiles({
                    "/etc/cfn/cfn-hup.conf": cloudformation.InitFile(
                        content=Join("", [
                            "[main]\n",
                            "stack=",
                            Ref(AWS_STACK_ID),
                            "\n",
                            "region=",
                            Ref(AWS_REGION),
                            "\n",
                        ]),
                        mode="000400",
                        owner="root",
                        group="root",
                    ),
                    "/etc/cfn/hooks.d/cfn-auto-reloader.conf":
                    cloudformation.InitFile(
                        content=Join("", [
                            "[cfn-auto-reloader-hook]\n",
                            "triggers=post.update\n",
                            "path=Resources.%s."
                            % container_instance_configuration_name,
                            "Metadata.AWS::CloudFormation::Init\n",
                            "action=/opt/aws/bin/cfn-init -v ",
                            "         --stack ",
                            Ref(AWS_STACK_NAME),
                            "         --resource %s"
                            % container_instance_configuration_name,
                            "         --region ",
                            Ref("AWS::Region"),
                            "\n",
                            "runas=root\n",
                        ])
                    )
                }),
                services=dict(
                    sysvinit=cloudformation.InitServices({
                        'cfn-hup': cloudformation.InitService(
                            enabled=True,
                            ensureRunning=True,
                            files=[
                                "/etc/cfn/cfn-hup.conf",
                                "/etc/cfn/hooks.d/cfn-auto-reloader.conf",
                            ]
                        ),
                    })
                )
            )
        ))
    ),
    SecurityGroups=[Ref(container_security_group)],
    InstanceType=container_instance_type,
    ImageId=FindInMap("ECSRegionMap", Ref(AWS_REGION), "AMI"),
    IamInstanceProfile=Ref(container_instance_profile),
    UserData=Base64(Join('', [
        "#!/bin/bash -xe\n",
        "yum install -y aws-cfn-bootstrap\n",
        "/opt/aws/bin/cfn-init -v ",
        "         --stack ", Ref(AWS_STACK_NAME),
        "         --resource %s " % container_instance_configuration_name,
        "         --region ", Ref(AWS_REGION), "\n",
        "/opt/aws/bin/cfn-signal -e $? ",
        "         --stack ", Ref(AWS_STACK_NAME),
        "         --resource %s " % container_instance_configuration_name,
        "         --region ", Ref(AWS_REGION), "\n",
    ])),
)


autoscaling_group = autoscaling.AutoScalingGroup(
    autoscaling_group_name,
    template=template,
    VPCZoneIdentifier=[Ref(container_a_subnet), Ref(container_b_subnet)],
    MinSize=desired_container_instances,
    MaxSize=max_container_instances,
    DesiredCapacity=desired_container_instances,
    LaunchConfigurationName=Ref(container_instance_configuration),
    LoadBalancerNames=[Ref(load_balancer)],
    # Since one instance within the group is a reserved slot
    # for rolling ECS service upgrade, it's not possible to rely
    # on a "dockerized" `ELB` health-check, else this reserved
    # instance will be flagged as `unhealthy` and won't stop respawning'
    HealthCheckType="EC2",
    HealthCheckGracePeriod=300,
)


web_log_group = logs.LogGroup(
    "WebLogs",
    template=template,
    RetentionInDays=365,
    DeletionPolicy="Retain",
)


# ECS task
web_task_definition = TaskDefinition(
    "WebTask",
    template=template,
    Condition=deploy_condition,
    ContainerDefinitions=[
        ContainerDefinition(
            Name="WebWorker",
            #  1024 is full CPU
            Cpu=web_worker_cpu,
            Memory=web_worker_memory,
            Essential=True,
            Image=Join("", [
                Ref(AWS_ACCOUNT_ID),
                ".dkr.ecr.",
                Ref(AWS_REGION),
                ".amazonaws.com/",
                Ref(repository),
                ":",
                app_revision,
            ]),
            PortMappings=[PortMapping(
                ContainerPort=web_worker_port,
                HostPort=web_worker_port,
            )],
            LogConfiguration=LogConfiguration(
                LogDriver="awslogs",
                Options={
                    'awslogs-group': Ref(web_log_group),
                    'awslogs-region': Ref(AWS_REGION),
                    'awslogs-stream-prefix': Ref(AWS_STACK_NAME),
                }
            ),
            Environment=[
                Environment(Name=k, Value=v)
                for k, v in environment_variables
            ] + [
                Environment(Name="PORT", Value=web_worker_port),
            ],
        )
    ],
)


app_service_role = iam.Role(
    "AppServiceRole",
    template=template,
    AssumeRolePolicyDocument=dict(Statement=[dict(
        Effect="Allow",
        Principal=dict(Service=["ecs.amazonaws.com"]),
        Action=["sts:AssumeRole"],
    )]),
    Path="/",
    Policies=[
        iam.Policy(
            PolicyName="WebServicePolicy",
            PolicyDocument=dict(
                Statement=[dict(
                    Effect="Allow",
                    Action=[
                        "elasticloadbalancing:Describe*",
                        "elasticloadbalancing"
                        ":DeregisterInstancesFromLoadBalancer",
                        "elasticloadbalancing"
                        ":RegisterInstancesWithLoadBalancer",
                        "ec2:Describe*",
                        "ec2:AuthorizeSecurityGroupIngress",
                    ],
                    Resource="*",
                )],
            ),
        ),
    ]
)


app_service = Service(
    "AppService",
    template=template,
    Cluster=Ref(cluster),
    Condition=deploy_condition,
    DependsOn=[autoscaling_group_name],
    DesiredCount=web_worker_desired_count,
    LoadBalancers=[LoadBalancer(
        ContainerName="WebWorker",
        ContainerPort=web_worker_port,
        LoadBalancerName=Ref(load_balancer),
    )],
    TaskDefinition=Ref(web_task_definition),
    Role=Ref(app_service_role),
)