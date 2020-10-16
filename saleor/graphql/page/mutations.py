from collections import defaultdict

import graphene
from django.core.exceptions import ValidationError

from ...core.permissions import PagePermissions, PageTypePermissions
from ...page import models
from ...page.error_codes import PageErrorCode
from ...product import AttributeType
from ..core.mutations import ModelDeleteMutation, ModelMutation
from ..core.types.common import PageError, SeoInput
from ..core.utils import (
    clean_seo_fields,
    get_duplicates_ids,
    validate_slug_and_generate_if_needed,
)


class PageInput(graphene.InputObjectType):
    slug = graphene.String(description="Page internal name.")
    title = graphene.String(description="Page title.")
    content = graphene.String(
        description=("Page content. May consist of ordinary text, HTML and images.")
    )
    content_json = graphene.JSONString(description="Page content in JSON format.")
    is_published = graphene.Boolean(
        description="Determines if page is visible in the storefront."
    )
    publication_date = graphene.String(
        description="Publication date. ISO 8601 standard."
    )
    seo = SeoInput(description="Search engine optimization fields.")


class PageCreate(ModelMutation):
    class Arguments:
        input = PageInput(
            required=True, description="Fields required to create a page."
        )

    class Meta:
        description = "Creates a new page."
        model = models.Page
        permissions = (PagePermissions.MANAGE_PAGES,)
        error_type_class = PageError
        error_type_field = "page_errors"

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)
        try:
            cleaned_input = validate_slug_and_generate_if_needed(
                instance, "title", cleaned_input
            )
        except ValidationError as error:
            error.code = PageErrorCode.REQUIRED
            raise ValidationError({"slug": error})
        clean_seo_fields(cleaned_input)
        return cleaned_input


class PageUpdate(PageCreate):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a page to update.")
        input = PageInput(
            required=True, description="Fields required to update a page."
        )

    class Meta:
        description = "Updates an existing page."
        model = models.Page
        permissions = (PagePermissions.MANAGE_PAGES,)
        error_type_class = PageError
        error_type_field = "page_errors"


class PageDelete(ModelDeleteMutation):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a page to delete.")

    class Meta:
        description = "Deletes a page."
        model = models.Page
        permissions = (PagePermissions.MANAGE_PAGES,)
        error_type_class = PageError
        error_type_field = "page_errors"


class PageTypeCreateInput(graphene.InputObjectType):
    name = graphene.String(description="Name of the page type.")
    slug = graphene.String(description="Page type slug.")
    add_attributes = graphene.List(
        graphene.NonNull(graphene.ID),
        description="List of attribute IDs to be assigned to the page type.",
    )


class PageTypeUpdateInput(PageTypeCreateInput):
    remove_attributes = graphene.List(
        graphene.NonNull(graphene.ID),
        description="List of attribute IDs to be assigned to the page type.",
    )


class PageTypeMixin:
    @classmethod
    def validate_attributes(cls, errors: dict, attributes, field):
        """All attributes must be page type attribute.

        Raise an error if any of the attributes are not page attribute.
        """
        if attributes:
            not_valid_attributes = [
                graphene.Node.to_global_id("Attribute", attr.pk)
                for attr in attributes
                if attr.type != AttributeType.PAGE_TYPE
            ]
            if not_valid_attributes:
                error = ValidationError(
                    "Only page type attributes allowed.",
                    code=PageErrorCode.INVALID.value,
                    params={"attributes": not_valid_attributes},
                )
                errors[field].append(error)


class PageTypeCreate(PageTypeMixin, ModelMutation):
    class Arguments:
        input = PageTypeCreateInput(
            description="Fields required to create page type.", required=True
        )

    class Meta:
        description = "Create a new page type."
        model = models.PageType
        permissions = (PageTypePermissions.MANAGE_PAGE_TYPES_AND_ATTRIBUTES,)
        error_type_class = PageError
        error_type_field = "page_errors"

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)
        errors = defaultdict(list)
        try:
            cleaned_input = validate_slug_and_generate_if_needed(
                instance, "name", cleaned_input
            )
        except ValidationError as error:
            error.code = PageErrorCode.REQUIRED.value
            errors["slug"].append(error)

        cls.validate_attributes(
            errors, cleaned_input.get("add_attributes"), "add_attributes"
        )

        if errors:
            raise ValidationError(errors)

        return cleaned_input

    @classmethod
    def _save_m2m(cls, info, instance, cleaned_data):
        super()._save_m2m(info, instance, cleaned_data)
        attributes = cleaned_data.get("add_attributes")
        if attributes is not None:
            instance.page_attributes.add(*attributes)


class PageTypeUpdate(PageTypeMixin, ModelMutation):
    class Arguments:
        id = graphene.ID(description="ID of the page type to update.")
        input = PageTypeUpdateInput(
            description="Fields required to update page type.", required=True
        )

    class Meta:
        description = "Update page type."
        model = models.PageType
        permissions = (PageTypePermissions.MANAGE_PAGE_TYPES_AND_ATTRIBUTES,)
        error_type_class = PageError
        error_type_field = "page_errors"

    @classmethod
    def check_for_duplicates(cls, errors, add_attributes, remove_attributes):
        """Check if any items are on both list for adding and removing.

        Raise error if some of items are duplicated.
        """
        duplicated_ids = get_duplicates_ids(add_attributes, remove_attributes)
        if duplicated_ids:
            error_msg = (
                "The same object cannot be in both list"
                "for adding and removing items."
            )
            error = ValidationError(
                error_msg,
                code=PageErrorCode.DUPLICATED_INPUT_ITEM.value,
                params={"attributes": duplicated_ids},
            )
            errors["attributes"].append(error)

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)
        errors = defaultdict(list)
        try:
            cleaned_input = validate_slug_and_generate_if_needed(
                instance, "name", cleaned_input
            )
        except ValidationError as error:
            error.code = PageErrorCode.REQUIRED
            errors["slug"].append(error)

        add_attributes = cleaned_input.get("add_attributes")
        cls.validate_attributes(errors, add_attributes, "add_attributes")

        remove_attributes = cleaned_input.get("remove_attributes")
        cls.validate_attributes(errors, remove_attributes, "remove_attributes")

        # TODO: move it up to operate on ids not on attributes
        cls.check_for_duplicates(
            errors, cleaned_input.get("add_attributes"), remove_attributes
        )

        if errors:
            raise ValidationError(errors)

        return cleaned_input

    @classmethod
    def _save_m2m(cls, info, instance, cleaned_data):
        super()._save_m2m(info, instance, cleaned_data)
        attributes = cleaned_data.get("remove_attributes")
        attributes = cleaned_data.get("add_attributes")
        if attributes is not None:
            instance.page_attributes.remove(*attributes)
        if attributes is not None:
            instance.page_attributes.add(*attributes)
