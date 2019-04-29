#!/usr/bin/env python
# -*- coding: utf-8 -*-
# (c) Copyright IBM Corp. 2010, 2019. All Rights Reserved.
# pylint: disable=line-too-long

"""Python Module to handle resilient-circuits ext: commands"""

import logging
import os
import json
import re
import shutil
import hashlib
import uuid
import sys
import base64
import importlib
import struct
from setuptools import sandbox as use_setuptools
import pkg_resources
from jinja2 import Environment, PackageLoader
from resilient_circuits.util.resilient_customize import ImportDefinition

# TODO: this logging is not working when commands ran on the command line
# It does work when ran in VS Code debugger!
# Setup logger. '__main__' is the name of the parent logger set in resilient_circuits_cmd.py
LOG = logging.getLogger("__main__")
# TODO: Add LOG.debug statements

# Custom Extensions Exception
class ExtException(Exception):
    """Custom Exception for ext commands"""
    def __init__(self, message):
        self.message = "\nresilient-circuits %s FAILED\nERROR: %s" % (ExtCommands.command_ran, message)

        # Call the base class
        super(ExtException, self).__init__(message)

    def __str__(self):
        return self.message


class ExtCommands(object):
    """Class that handles ext:package and ext:convert commands"""

    # Class variable to store what command was run
    command_ran = None

    # Tuple of the setup.py attributes we use
    supported_setup_py_attribute_names = (
        "author", "name", "version",
        "description", "long_description", "url"
    )

    # Tuple of all Resilient Object Names we support when packaging/converting
    supported_res_obj_names = (
        "actions", "automatic_tasks", "fields",
        "functions", "incident_artifact_types",
        "incident_types", "message_destinations",
        "phases", "roles", "scripts",
        "types", "workflows", "workspaces"
    )

    # Instansiate Jinja2 Environment with path to Jinja2 templates
    jinja_env = Environment(
        loader=PackageLoader("resilient_circuits", "data/ext/templates")
    )

    def __init__(self, cmd, path_to_extension, display_name=None, keep_build_dir=False):

        # Set command_ran class variable
        ExtCommands.command_ran = cmd

        if cmd == "ext:package":
            self.package_extension(path_to_extension, custom_display_name=display_name, keep_build_dir=keep_build_dir)

        elif cmd == "ext:convert":
            # TODO
            # Validate the .zip or .tar.gz file passed
            # Be smart enough to check the structure of the .zip and find the "old integration" tarball
            # Create a tmp directory
            # Extract the .zip/.tar.gz into tmp
            # Call package:ext on the tmp dir
            pass

        else:
            raise ExtException("Unsupported command: {0}. Supported commands are: (ext:package, ext:convert)")

    @staticmethod
    def __write_file__(path, contents):
        """Writes the String contents to a file at path"""
        with open(path, "wb") as the_file:
            the_file.write(contents)

    @staticmethod
    def __read_file__(path):
        """Returns all the lines of a file at path as a List"""
        file_lines = []
        with open(path, "r") as the_file:
            file_lines = the_file.readlines()
        return file_lines

    @staticmethod
    def __is_valid_url__(url):
        """Returns True if url is valid, else False. Accepted url examples are:
            "http://www.example.com", "https://www.example.com", "www.example.com", "example.com" """

        if not url:
            return False

        regex = re.compile(
            r'^(https?://)?'  # optional http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?)'  # domain/hostname
            r'(?:/?|[/?]\S+)$', # .com etc.
            re.IGNORECASE)

        return regex.search(url) is not None

    @staticmethod
    def __is_valid_package_name__(name):
        """Returns True if name is valid, else False. Accepted name examples are:
            "fn_my_new_package" """

        if not name:
            return False

        regex = re.compile(r'^[0-9A-Z_]+$', re.IGNORECASE)

        return regex.match(name) is not None

    @staticmethod
    def __is_valid_version_syntax__(version):
        """Returns True if version is valid, else False. Accepted version examples are:
            "1.0.0" "1.1.0" "123.0.123" """
        if not version:
            return False

        regex = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+$')

        return regex.match(version) is not None

    @staticmethod
    def __generate_md5_uuid_from_file__(path_to_file):
        """Returns String representation of the UUID of a hex md5 hash of the given file"""

        # Instansiate new md5_hash
        md5_hash = hashlib.md5()

        # Open a stream to the file. Read 4096 bytes at a time. Pass these bytes to md5_hash object
        with open(path_to_file, mode="rb") as f:
            for chunk in iter(lambda: f.read(4096), b''):
                md5_hash.update(chunk)

        # Generate the hex md5 hash of all the read bytes
        the_md5_hex_str = md5_hash.hexdigest()

        # Return a String repersenation of the uuid of the md5 hash
        return str(uuid.UUID(the_md5_hex_str))

    @staticmethod
    def __has_permissions__(permissions, path):
        """Raises an exception if the user does not have the given permissions to path"""

        if not os.access(path, permissions):

            if permissions is os.R_OK:
                permissions = "READ"
            elif permissions is os.W_OK:
                permissions = "WRITE"

            raise ExtException("User does not have {0} permissions for: {1}".format(permissions, path))

    @classmethod
    def __validate_directory__(cls, permissions, path_to_dir):
        """Check the given path is absolute, exists and has the given permissions, else raises an Exception"""

        # Check the path is absolute
        if not os.path.isabs(path_to_dir):
            raise ExtException("The path to the directory must be an absolute path: {0}".format(path_to_dir))

        # Check the directory exists
        if not os.path.isdir(path_to_dir):
            raise ExtException("The path does not exist: {0}".format(path_to_dir))

        # Check we have the correct permissions
        cls.__has_permissions__(permissions, path_to_dir)

    @classmethod
    def __validate_file_paths__(cls, permissions=None, *args):
        """Check the given *args paths exist and has the given permissions, else raises an Exception"""

        # For each *args
        for path_to_file in args:
            # Check the file exists
            if not os.path.isfile(path_to_file):
                raise ExtException("Could not find file: {0}".format(path_to_file))

            if permissions:
                # Check we have the correct permissions
                cls.__has_permissions__(permissions, path_to_file)

    @staticmethod
    def __is_setup_attribute__(line):
        """Use RegEx to check if the given file line starts with (for example) 'long_description='.
        Will also handle if the attribute has been commented out: '# long_description='.
        Returns True if something like 'long_description=' is at the start of the line, else False"""

        any_attribute_regex = re.compile(r'^#?\s*[a-z_]+=')

        if re.match(pattern=any_attribute_regex, string=line) is not None:
            return True

        return False

    @classmethod
    def __parse_setup_attribute__(cls, path_to_setup_py, setup_py_lines, attribute_name):
        """Returns the attribute value from setup.py given the attribute name"""

        # Generate the regex to find the attribute line
        the_attribute_regex = re.compile("^{0}=".format(attribute_name))

        # Store the contents of the attribute
        the_attribute_found, the_attribute_value = False, []

        # Loop the setup_py lines
        for i in range(len(setup_py_lines)):
            the_attribute_line = setup_py_lines[i]

            # Check if this line contains the attribute_name
            if re.match(pattern=the_attribute_regex, string=the_attribute_line):

                # If found, flip flag + append the line to the_attribute_value
                the_attribute_found = True
                the_attribute_value.append(re.split(pattern=the_attribute_regex, string=the_attribute_line)[1])

                # Check preceding lines to see if this attribute value is multiline string
                for preceding_line in setup_py_lines[i+1:]:
                    if cls.__is_setup_attribute__(preceding_line):
                        break

                    # Append the line if it is not a new attribute
                    the_attribute_value.append(preceding_line)

                break

        # If we could not find an attribute with attribute_name, raise an Exception
        if not the_attribute_found:
            LOG.warning("WARNING: '%s' is not a defined attribute name in the provided setup.py file: %s", attribute_name, path_to_setup_py)

        # Create single string and trim (" , ' )
        the_attribute_value = " ".join(the_attribute_value)
        the_attribute_value = the_attribute_value.strip("\",'.")

        return the_attribute_value

    @classmethod
    def __parse_setup_py__(cls, path, attribute_names):
        """Parse the values of the given attribute_names and return a Dictionary attribute_name:attribute_value"""

        # Read the setup.py file into a List
        setup_py_lines = cls.__read_file__(path)

        # Raise an error is nothing found in the file
        if not setup_py_lines:
            raise ExtException("No content found in provided setup.py file: {0}".format(path))

        setup_regex_pattern = r"setup\("
        setup_defined, index_of_setup, return_dict = False, None, dict()

        for i in range(len(setup_py_lines)):

            if re.match(pattern=setup_regex_pattern, string=setup_py_lines[i]) is not None:
                setup_defined = True
                index_of_setup = i
                break

        # Raise an error if we can't find 'setup()' in the file
        if not setup_defined:
            raise ExtException("Could not find 'setup()' defined in provided setup.py file: {0}".format(path))

        # Get sublist containing lines from 'setup(' to EOF + trim whitespaces
        setup_py_lines = setup_py_lines[index_of_setup:]
        setup_py_lines = [file_line.strip() for file_line in setup_py_lines]

        # Foreach attribute_name, get its value and add to return_dict
        for attribute_name in attribute_names:
            return_dict[attribute_name] = cls.__parse_setup_attribute__(path, setup_py_lines, attribute_name)

        return return_dict

    @staticmethod
    def __get_import_definition_from_customize_py__(path_to_base_dir):
        """Return the base64 encoded ImportDefinition in a customize.py file as a Dictionary"""

        # Insert the path_to_base_dir to the start of our Python PATH at runtime so we can import the customize module from within it
        sys.path.insert(0, path_to_base_dir)

        # TODO: investigate why customize.py has to "from resilient_circuits.util import *"??
        # TODO: investigate why resilient-circuits customize command adds a default 'incident_type' and 'field'. Need to remove it. Can do by uuid
        # Import the customize module
        customize_py = importlib.import_module("util.customize")

        # Call customization_data() to get all ImportDefinitions that are "yielded"
        customize_py_import_definitions_generator = customize_py.customization_data()
        customize_py_import_definitions = []

        # customization_data() returns a Generator object with all yield statements, so we loop them
        for definition in customize_py_import_definitions_generator:
            if isinstance(definition, ImportDefinition):
                customize_py_import_definitions.append(json.loads(base64.b64decode(definition.value)))
            else:
                LOG.warning("WARNING: Unsupported data found in customize.py file. Expected an ImportDefinition. Got: '%s'", definition)

        # If no ImportDefinition found
        if not customize_py_import_definitions:
            raise ExtException("No ImportDefinition found in the customize.py file")

        # If more than 1 found
        elif len(customize_py_import_definitions) > 1:
            raise ExtException("Multiple ImportDefinitions found in the customize.py file. There must only be 1 ImportDefinition defined")

        return customize_py_import_definitions[0]

    @classmethod
    def __get_icon__(cls, path_to_icon, width_accepted, height_accepted, default_path_to_icon):
        """Returns the icon at path_to_icon as a base64 encoded string if it is a valid .png file with the resolution
        width_accepted x height_accepted. If path_to_icon does not exist, default_path_to_icon is returned as a base64
        encoded string"""

        path_icon_to_use = path_to_icon

        # Use default_path_to_icon is path_to icon does not exist
        if not os.path.isfile(path_icon_to_use):
            path_icon_to_use = default_path_to_icon

        # Validate path_icon_to_use and ensure we have READ permissions
        try:
            cls.__validate_file_paths__(os.R_OK, path_icon_to_use)
        except ExtException as err:
            raise OSError("Could not find valid icon file. Looked at two locations:\n{0}\n{1}\n{2}".format(path_to_icon, default_path_to_icon, err.message))

        # Get the extension of the file. os.path.splitext returns a Tuple with the file extension at position 1 and can be an empty string
        split_path = os.path.splitext(path_icon_to_use)
        file_extension = split_path[1]

        if not file_extension:
            raise ExtException("Provided icon file does not have an extension. Icon file must be .png\nIcon File: {0}".format(path_icon_to_use))

        elif file_extension != ".png":
            raise ExtException("{0} is not a supported icon file type. Icon file must be .png\nIcon File: {1}".format(file_extension, path_icon_to_use))

        # Open the icon_file in Bytes mode to validate its resolution
        with open(path_icon_to_use, mode="rb") as icon_file:
            # According to: https://en.wikipedia.org/wiki/Portable_Network_Graphics#File_format
            # First need to seek 16 bytes:
            #   8 bytes: png signature
            #   4 bytes: IDHR Chunk Length
            #   4 bytes: IDHR Chunk type
            icon_file.seek(16)

            try:
                # Bytes 17-20 = image width. Use struct to unpack big-endian encoded unsigned int
                icon_width = struct.unpack(">I", icon_file.read(4))[0]

                # Bytes 21-24 = image height. Use struct to unpack big-endian encoded unsigned int
                icon_height = struct.unpack(">I", icon_file.read(4))[0]
            except Exception as err:
                raise ExtException("Failed to read icon's resolution. Icon file corrupt. Icon file must be .png\nIcon File: {1}".format(path_icon_to_use))

        # Raise exception if resolution is not accepted
        if icon_width != width_accepted or icon_height != height_accepted:
            raise ExtException("Icon resolution is {0}x{1}. Resolution must be {2}x{3}\nIcon File:{4}".format(icon_width, icon_height, width_accepted, height_accepted, path_icon_to_use))

        # If we get here all validations have passed. Open the file in Bytes mode and encode it as base64
        with open(path_icon_to_use, "rb") as icon_file:
            encoded_icon_string = base64.b64encode(icon_file.read())

        return encoded_icon_string

    @staticmethod
    def __add_tag__(tag_name, list_of_objs):
        """Returns list_of_objs with tag_name added to each object"""

        # Create tag_to_add
        tag_to_add = {
            "tag_handle": tag_name,
            "value": None
        }

        err_msg = "Error adding tag '{0}'. '{1}' (printed above) is not a {2}. Instead a {3} was provided.\nProvided ImportDefinition in the customize.py file may be corrupt"

        # Check list_of_objs is a List
        if not isinstance(list_of_objs, list):
            LOG.error("Error adding tag.\n'list_of_objs': %s", list_of_objs)
            raise ExtException(err_msg.format(tag_name, "list_of_objs", "List", type(list_of_objs)))

        # Loop each object in the List
        for obj in list_of_objs:

            # If its not a dict, error
            if not isinstance(obj, dict):
                LOG.error("Error adding tag.\n'list_of_objs': %s\n'obj': %s", list_of_objs, obj)
                raise ExtException(err_msg.format(tag_name, "obj", "Dictionary", type(obj)))

            # Try get current_tags
            current_tags = obj.get("tags")

            # If None, create new empty List
            if current_tags is None:
                current_tags = []

            # If current_tags is not a list, error
            if not isinstance(current_tags, list):
                LOG.error("Error adding tag.\n'current_tags': %s", current_tags)
                raise ExtException(err_msg.format(tag_name, "current_tags", "List", type(current_tags)))

            # Append our tag_to_add to current_tags
            current_tags.append(tag_to_add)

            # Set the obj's 'tags' value to current_tags
            obj["tags"] = current_tags

        # Return the updated list_of_objs
        return list_of_objs

    @classmethod
    def __add_tag_to_import_definition__(cls, tag_name, supported_res_obj_names, import_definition):
        """Returns import_definition with tag_name added to each supported_res_object_name found
        in the import_definition"""

        for obj_name in supported_res_obj_names:

            res_object_list = import_definition.get(obj_name)

            if res_object_list:
                res_object_list = cls.__add_tag__(tag_name, res_object_list)

                # A 'function' object has a list of 'workflows' which also need the tag added to
                if obj_name == "functions":
                    res_functions_list = import_definition.get("functions")

                    for fn in res_functions_list:
                        fn_workflows_list = fn.get("workflows")

                        if fn_workflows_list:
                            fn_workflows_list = cls.__add_tag__(tag_name, fn_workflows_list)

        return import_definition

    @classmethod
    def package_extension(cls, path_to_extension, custom_display_name=None, keep_build_dir=False):

        LOG.info("Packaging extension. args: %s", path_to_extension)

        # Generate path to setup.py, base dir and customize.py
        path_setup_py_file = os.path.join(path_to_extension, "setup.py")
        path_to_base_dir = os.path.join(path_to_extension, os.path.basename(path_to_extension))
        path_customize_py_file = os.path.join(path_to_base_dir, "util", "customize.py")

        # Ensure the directory exists, we have WRITE access and ensure we can READ setup.py and customize.py
        cls.__validate_directory__(os.W_OK, path_to_extension)
        cls.__validate_file_paths__(os.R_OK, path_setup_py_file, path_customize_py_file)

        # Parse the setup.py file
        setup_py_attributes = cls.__parse_setup_py__(path_setup_py_file, cls.supported_setup_py_attribute_names)

        # Validate setup.py attributes

        # Validate the name attribute. Raise exception if invalid
        if not cls.__is_valid_package_name__(setup_py_attributes.get("name")):
            raise ExtException("'{0}' is not a valid Extension name. The name attribute must be defined and can only include 'a-z and _'.\nUpdate this value in the setup.py file located at: {1}".format(setup_py_attributes.get("name"), path_setup_py_file))

        # Validate the version attribute. Raise exception if invalid
        if not cls.__is_valid_version_syntax__(setup_py_attributes.get("version")):
            raise ExtException("'{0}' is not a valid Extension version syntax. The version attribute must be defined. Example: version=\"1.0.0\".\nUpdate this value in the setup.py file located at: {1}".format(setup_py_attributes.get("version"), path_setup_py_file))

        # Validate the url supplied in the setup.py file, set to an empty string if not valid
        if not cls.__is_valid_url__(setup_py_attributes.get("url")):
            LOG.warning("WARNING: URL specified in the setup.py file is not valid. '%s' is not a valid url. Ignoring.", setup_py_attributes.get("url"))
            setup_py_attributes["url"] = ""

        # Generate the 'main' name for the extension
        extension_name = "{0}-{1}".format(setup_py_attributes.get("name"), setup_py_attributes.get("version"))

        # Get the tag name
        tag_name = setup_py_attributes.get("name")

        # Generate all paths to the directories and files we will use
        path_dist = os.path.join(path_to_extension, "dist")
        path_python_tar_package = os.path.join(path_dist, "{0}.tar.gz".format(extension_name))
        path_extension_zip = os.path.join(path_dist, "ext-{0}".format(extension_name))
        path_build = os.path.join(path_dist, "build")
        path_extension_json = os.path.join(path_build, "extension.json")
        path_export_res = os.path.join(path_build, "export.res")
        path_executables = os.path.join(path_build, "executables")
        path_executable_zip = os.path.join(path_executables, "exe-{0}".format(extension_name))
        path_executable_json = os.path.join(path_executable_zip, "executable.json")
        path_executable_dockerfile = os.path.join(path_executable_zip, "Dockerfile")

        # Get ImportDefinition from customize.py
        customize_py_import_definition = cls.__get_import_definition_from_customize_py__(path_to_base_dir)

        # Add the tag to the import defintion
        customize_py_import_definition = cls.__add_tag_to_import_definition__(tag_name, cls.supported_res_obj_names, customize_py_import_definition)

        try:
            # Create the directories for the path "./dist/build/executables/exe-<package-name>/"
            os.makedirs(path_executable_zip)

            # Generate the contents for the executable.json file
            the_executable_json_file_contents = {
                "name": extension_name
            }

            # Write the executable.json file
            cls.__write_file__(path_executable_json, json.dumps(the_executable_json_file_contents, sort_keys=True))

            # Load Dockerfile template
            docker_file_template = cls.jinja_env.get_template("docker_file_template.jinja2")

            # Render Dockerfile template with required variables
            the_dockerfile_contents = docker_file_template.render({
                "extension_name": extension_name,
                "installed_package_name": setup_py_attributes.get("name").replace("_", "-")
            })

            # Write the Dockerfile
            cls.__write_file__(path_executable_dockerfile, the_dockerfile_contents)

            # TODO: avoid all the logs that get printed with this command
            # Confirm the need for the .egg files
            # Ensure all files in the tar.gz are needed and correct
            # Generate the tar.gz
            use_setuptools.run_setup(setup_script=path_setup_py_file, args=["sdist"])

            # Copy the tar.gz to the executable_zip dir
            shutil.copy(path_python_tar_package, path_executable_zip)

            # zip the executable_zip dir
            shutil.make_archive(base_name=path_executable_zip, format="zip", root_dir=path_executable_zip)

            # Remove the executable_zip dir
            shutil.rmtree(path_executable_zip)

            # Get the extension_logo (icon) and company_logo (author.icon) as base64 encoded strings
            extension_logo = cls.__get_icon__(
                path_to_icon=os.path.join(path_to_extension, "icons", "extension_logo.png"),
                width_accepted=200,
                height_accepted=72,
                default_path_to_icon=pkg_resources.resource_filename("resilient_circuits", "data/ext/icons/extension_logo.png"))

            company_logo = cls.__get_icon__(
                path_to_icon=os.path.join(path_to_extension, "icons", "company_logo.png"),
                width_accepted=100,
                height_accepted=100,
                default_path_to_icon=pkg_resources.resource_filename("resilient_circuits", "data/ext/icons/company_logo.png"))

            # Generate the contents for the extension.json file
            the_extension_json_file_contents = {
                "author": {
                    "name": setup_py_attributes.get("author"),
                    "website": setup_py_attributes.get("url"),
                    "icon": {
                        "data": company_logo,
                        "media_type": "image/png"
                    }
                },
                "description": {
                    "content": setup_py_attributes.get("description"),
                    "format": "text"
                },
                "display_name": custom_display_name if custom_display_name is not None else setup_py_attributes.get("name"),
                "icon": {
                    "data": extension_logo,
                    "media_type": "image/png"
                },
                "long_description": {
                    "content": "<div>{0}</div>".format(setup_py_attributes.get("long_description")),
                    "format": "html"
                },
                "minimum_resilient_version": {
                    "major": customize_py_import_definition.get("server_version").get("major"),
                    "minor": customize_py_import_definition.get("server_version").get("minor"),
                    "build_number": customize_py_import_definition.get("server_version").get("build_number"),
                    "version": customize_py_import_definition.get("server_version").get("version")
                },
                "name": setup_py_attributes.get("name"),
                "tag": {
                    "prefix": tag_name, # TODO: What, where and why is this used?
                    "name": tag_name,
                    "display_name": tag_name,
                    "uuid": cls.__generate_md5_uuid_from_file__(path_python_tar_package) # TODO: if a developer changes FunctionComponent code, this uuid will change
                },
                "uuid": cls.__generate_md5_uuid_from_file__("{0}.zip".format(path_executable_zip)), # TODO: if a developer changes FunctionComponent code, this uuid will change. Use package-name/author instead
                "version": setup_py_attributes.get("version")
            }

            # Write the executable.json file
            cls.__write_file__(path_extension_json, json.dumps(the_extension_json_file_contents, sort_keys=True))

            # Write the customize ImportDefinition to the export.res file
            cls.__write_file__(path_export_res, json.dumps(customize_py_import_definition, sort_keys=True))

            # zip the build dir
            shutil.make_archive(base_name=path_extension_zip, format="zip", root_dir=path_build)

        except Exception as err:
            # If the .tar.gz file has been generated, delete it
            if os.path.isfile(path_python_tar_package):
                os.remove(path_python_tar_package)

            raise Exception(err.message)

        finally:
            # Remove the executable_zip dir. Keep it if user passes --keep-build-dir
            if not keep_build_dir:
                shutil.rmtree(path_build)
