import kopf
import time
import kubernetes.client
import os
import yaml
import logging
import re

from kubernetes.client.rest import ApiException
from kubernetes.client import ApiClient


def get_config(file):
    try:
        with open(file, "r") as ymlfile:
            cfg = yaml.load(ymlfile, Loader=yaml.SafeLoader)
            return cfg
    except:
        return {}


def create_dns_record(name, namespace, spec, status, logger, **_):
    period = 5  # time to waith before retry
    timeout = 300  # total timeout of gettin an IP
    ips = ""
    path = os.path.join(os.path.dirname(__file__), "DNSRecordSet.yaml")
    config = get_config("./config/dns.yaml")
    text = open(path, "rt").read()
    # update model with correct values
    data = yaml.safe_load(text)
    host = spec["rules"][0]["host"]
    data["metadata"]["annotations"]["cnrm.cloud.google.com/project-id"] = config[
        "dns-project"
    ]["name"]
    data["spec"]["managedZoneRef"]["external"] = config["dns-zone"]["name"]
    data["metadata"]["name"] = name
    data["metadata"]["namespace"] = namespace
    # We add a point to be a valid DNS entry
    data["spec"]["name"] = host + "."
    mustend = time.time() + timeout
    api_instance_ingress = kubernetes.client.ExtensionsV1beta1Api()
    api_response = api_instance_ingress.read_namespaced_ingress_status(name, namespace)
    status = ApiClient().sanitize_for_serialization(api_response)
    if "ingress" in status["status"]["loadBalancer"]:
        ips = status["status"]["loadBalancer"]["ingress"]
    while time.time() < mustend or ips == "":
        # this if is not working because status is an object not a json
        if not "ingress" in status["status"]["loadBalancer"] and ips == "":
            logger.debug(
                f"No Ingress IP found yet for "
                + namespace
                + "/"
                + name
                + ", retrying... "
            )
            api_response = api_instance_ingress.read_namespaced_ingress_status(
                name, namespace
            )
            status = ApiClient().sanitize_for_serialization(api_response)
        else:
            # print("ip is " + str(status["loadBalancer"]["ingress"]))
            ips = status["status"]["loadBalancer"]["ingress"]
            break
        time.sleep(period)

    if ips != "":
        logger.info(f"a DNSRecordSet will be created: " + namespace + "/" + name)
        # need to loop here
        for ip in ips:
            data["spec"]["rrdatas"].append(ip["ip"])
        # data["spec"]["rrdatas"][0] = ip

        logger.debug(f"the DNSRecordSet for: " + namespace + "/" + name + " will be :")
        logger.debug(data)
        # create an instance of the API class
        api_instance = kubernetes.client.CustomObjectsApi()
        group = "dns.cnrm.cloud.google.com"  # str | The custom resource's group name
        version = "v1beta1"  # str | The custom resource's version
        plural = "dnsrecordsets"  # str | The custom resource's plural name. For TPRs this would be lowercase plural kind.
        body = data  # UNKNOWN_BASE_TYPE | The JSON schema of the Resource to create.
        pretty = "pretty_example"  # str | If 'true', then the output is pretty printed. (optional)

        try:
            api_response = api_instance.create_namespaced_custom_object(
                group, version, namespace, plural, body, pretty=pretty
            )
            logger.info(f"DNSRecordSet created: " + namespace + "/" + name)
        except ApiException as e:
            print(
                "Exception when calling CustomObjectsApi->create_namespaced_custom_object: %s\n"
                + namespace
                + "/"
                + name
                + "-" % e
            )
            logger.error(f"Unable to create DNSRecordSet: " + namespace + "/" + name)


def delete_dns_record(name, namespace, logger, **_):
    logger.info(f"DNSRecordSet will be deleted: " + namespace + "/" + name)

    # create an instance of the API class
    api_instance_del = kubernetes.client.CustomObjectsApi()
    group = "dns.cnrm.cloud.google.com"  # str | The custom resource's group name
    version = "v1beta1"  # str | The custom resource's version
    plural = "dnsrecordsets"  # str | The custom resource's plural name. For TPRs this would be lowercase plural kind.
    pretty = "pretty_example"  # str | If 'true', then the output is pretty printed. (optional)

    try:
        api_response = api_instance_del.delete_namespaced_custom_object(
            group, version, namespace, plural, name=name, body={}
        )
        logger.info(f"DNSRecordSet has been deleted: " + namespace + "/" + name)
    except ApiException as e:
        print(
            "Exception when calling CustomObjectsApi->delete_namespaced_custom_object: %s\n on namespace {0}".format(
                namespace
            )
            % e
        )
        logger.error(f"Unable to create DNSRecordSet: " + namespace + "/" + name)


def update_dns_record(name, namespace, spec, status, logger, **_):
    logger.info(f"Ingress has been modified: " + namespace + "/" + name)
    # we need the ingress spec here not the update spec
    api_instance_ingress = kubernetes.client.ExtensionsV1beta1Api()
    api_response = api_instance_ingress.read_namespaced_ingress(name, namespace)
    ingress_spec = ApiClient().sanitize_for_serialization(api_response)
    host = ingress_spec["spec"]["rules"][0]["host"]
    api_instance_upd = kubernetes.client.CustomObjectsApi()
    group = "dns.cnrm.cloud.google.com"  # str | The custom resource's group name
    version = "v1beta1"  # str | The custom resource's version
    plural = "dnsrecordsets"  # str | The custom resource's plural name. For TPRs this would be lowercase plural kind.
    pretty = "pretty_example"  # str | If 'true', then the output is pretty printed. (optional)
    try:
        api_response = api_instance_upd.get_namespaced_custom_object(
            group, version, namespace, plural, name=name
        )
        dnsrecord = ApiClient().sanitize_for_serialization(api_response)
    except ApiException as e:
        logger.error(
            f"Unable to find DNSRecordSet to update it, creating: "
            + namespace
            + "/"
            + name
        )
        create_dns_record(name, namespace, ingress_spec["spec"], status, logger)
        return
    # check if host has changed
    if host + "." != dnsrecord["spec"]["name"]:
        logger.info(
            f"Ingress host changed, updating DNSRecordSet: " + namespace + "/" + name
        )
        delete_dns_record(name, namespace, logger)
        time.sleep(5)
        create_dns_record(name, namespace, ingress_spec["spec"], status, logger)
        logger.info(f"DNSRecordSet deleted and recreated: " + namespace + "/" + name)
    else:
        logger.info(
            f"Ingress host remain the same, no change: " + namespace + "/" + name
        )


@kopf.on.create("", "extensions/v1beta1", "ingresses")
def ingress_created(name, namespace, spec, status, logger, **_):
    logger.info(f"an Ingress has been created: " + namespace + "/" + name)
    create_dns_record(name, namespace, spec, status, logger)


@kopf.on.delete("", "extensions/v1beta1", "ingresses")
def ingress_deleted(name, namespace, logger, **_):
    logger.info(f"an Ingress has been deleted: " + namespace + "/" + name)

    delete_dns_record(name, namespace, logger)


@kopf.on.update("", "extensions/v1beta1", "ingresses")
def ingress_modified(name, namespace, spec, status, logger, **_):
    update_dns_record(name, namespace, spec, status, logger)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    ingress_created("test", logging)

