from collections import OrderedDict
from datetime import datetime
import logging

from lazy.lazy import lazy
import pytz
from xblock.core import XBlock

from xblock.fields import Scope, String, DateTime

from xblock.fragment import Fragment

from xblock.validation import ValidationMessage

from xblockutils.studio_editable import StudioEditableXBlockMixin, StudioContainerXBlockMixin

from group_project_v2.api_error import ApiError
from group_project_v2.stage_components import (
    PeerSelectorXBlock, GroupProjectReviewQuestionXBlock, GroupProjectReviewAssessmentXBlock,
    GroupProjectResourceXBlock, GroupProjectSubmissionXBlock,
    StageState
)
from group_project_v2.project_api import project_api
from group_project_v2.utils import loader, format_date, gettext as _, ChildrenNavigationXBlockMixin

log = logging.getLogger(__name__)


class StageType(object):
    NORMAL = 'normal'
    UPLOAD = 'upload'
    PEER_REVIEW = 'peer_review'
    PEER_ASSESSMENT = 'peer_assessment'
    GROUP_REVIEW = 'group_review'
    GROUP_ASSESSMENT = 'group_assessment'


class ResourceType(object):
    NORMAL = 'normal'
    OOYALA_VIDEO = 'ooyala'


class BaseGroupActivityStage(XBlock, ChildrenNavigationXBlockMixin,
                             StudioEditableXBlockMixin, StudioContainerXBlockMixin):
    submissions_stage = False

    display_name = String(
        display_name=_(u"Display Name"),
        help=_(U"This is a name of the stage"),
        scope=Scope.settings,
        default="Group Project V2 Stage"
    )

    open_date = DateTime(
        display_name=_(u"Open Date"),
        help=_(u"Stage open date"),
        scope=Scope.settings
    )

    close_date = DateTime(
        display_name=_(u"Close Date"),
        help=_(u"Stage close date"),
        scope=Scope.settings
    )

    STAGE_WRAPPER_TEMPLATE = 'templates/html/stages/stage_wrapper.html'

    editable_fields = ('display_name', 'open_date', 'close_date')
    has_children = True
    has_score = False  # TODO: Group project V1 are graded at activity level. Check if we need to follow that

    @property
    def id(self):
        return self.scope_ids.usage_id

    @property
    def allowed_nested_blocks(self):
        """
        This property outputs an ordered dictionary of allowed nested XBlocks in form of block_category: block_caption.
        """
        return OrderedDict([
            ("html", _(u"HTML")),
            (GroupProjectResourceXBlock.CATEGORY, _(u"Resource"))
        ])

    @lazy
    def activity(self):
        return self.get_parent()

    @property
    def resources(self):
        return self._get_children_by_category(GroupProjectResourceXBlock.CATEGORY)

    @property
    def grading_criteria(self):
        return (resource for resource in self.resources if resource.grading_criteria)

    @property
    def formatted_open_date(self):
        return format_date(self.open_date)

    @property
    def formatted_close_date(self):
        return format_date(self.close_date)

    @property
    def is_open(self):
        return (self.open_date is None) or (self.open_date <= datetime.utcnow().replace(tzinfo=pytz.UTC))

    @property
    def is_closed(self):
        # If this stage is being loaded for the purposes of a TA grading,
        # then we never close the stage - in this way a TA can impose any
        # action necessary even if it has been closed to the group members
        if self.activity.is_admin_grader:
            return False

        return (self.close_date is not None) and (self.close_date < datetime.utcnow().replace(tzinfo=pytz.UTC))

    def student_view(self, context):
        stage_fragment = self.get_stage_content_fragment(context)

        fragment = Fragment()
        fragment.add_frag_resources(stage_fragment)
        render_context = {
            'stage': self, 'stage_content': stage_fragment.content,
            "ta_graded": self.activity.group_reviews_required_count
        }
        fragment.add_content(loader.render_template(self.STAGE_WRAPPER_TEMPLATE, render_context))

        return fragment

    def render_children_fragment(self, context, children=None, view='student_view'):
        to_render = children if children else self._children
        fragment = Fragment()

        for child in to_render:
            child_fragment = child.render(view, context)
            fragment.add_frag_resources(child_fragment)
            fragment.add_content(child_fragment.content)

        return fragment

    def get_stage_content_fragment(self, context):
        return self.render_children_fragment(context)

    def author_preview_view(self, context):
        return self.student_view(context)

    def author_edit_view(self, context):
        """
        Add some HTML to the author view that allows authors to add child blocks.
        """
        fragment = Fragment()
        self.render_children(context, fragment, can_reorder=True, can_add=False)
        fragment.add_content(
            loader.render_template('templates/html/add_buttons.html', {'child_blocks': self.allowed_nested_blocks})
        )
        fragment.add_css_url(self.runtime.local_resource_url(self, 'public/css/group_project_edit.css'))
        return fragment

    def mark_complete(self, user_id):
        try:
            project_api.mark_as_complete(self.activity.course_id, self.activity.content_id, user_id, self.id)
        except ApiError as e:
            # 409 indicates that the completion record already existed # That's ok in this case
            if e.code != 409:
                raise

    def get_stage_state(self):
        """
        Gets stage completion state
        """
        users_in_group, completed_users = project_api.get_stage_state(
            self.activity.course_id,
            self.activity.id,
            self.activity.user_id,
            self.id
        )

        if not users_in_group or not completed_users:
            return StageState.NOT_STARTED
        if users_in_group <= completed_users:
            return StageState.COMPLETED
        if users_in_group & completed_users:
            return StageState.INCOMPLETE
        else:
            return StageState.NOT_STARTED

    def navigation_view(self, context):
        fragment = Fragment()
        rendering_context = {
            'stage': self,
            'activity_id': self.activity.id,
            'stage_state': self.get_stage_state()
        }
        rendering_context.update(context)
        fragment.add_content(loader.render_template("templates/html/stages/navigation_view.html", rendering_context))
        return fragment

    def resources_view(self, context):
        fragment = Fragment()

        resource_contents = []
        for resource in self.resources:
            resource_fragment = resource.render('resources_view', context)
            fragment.add_frag_resources(resource_fragment)
            resource_contents.append(resource_fragment.content)

        context = {'stage': self, 'resource_contents': resource_contents}
        fragment.add_content(loader.render_template("templates/html/stages/resources_view.html", context))

        return fragment


class BasicStage(BaseGroupActivityStage):
    type = u'Text'
    CATEGORY = 'group-project-v2-stage-basic'


class SubmissionStage(BaseGroupActivityStage):
    type = u'Task'
    CATEGORY = 'group-project-v2-stage-submission'

    submissions_stage = True

    @property
    def allowed_nested_blocks(self):
        blocks = super(SubmissionStage, self).allowed_nested_blocks
        blocks.update(OrderedDict([
            (GroupProjectSubmissionXBlock.CATEGORY, _(u"Submission"))
        ]))
        return blocks

    @property
    def submissions(self):
        return self._get_children_by_category(GroupProjectSubmissionXBlock.CATEGORY)

    @property
    def is_upload_available(self):
        return self.submissions and self.is_open and not self.is_closed

    @property
    def has_submissions(self):
        return bool(self.submissions)  # explicitly converting to bool to indicate that it is bool property

    def validate(self):
        violations = super(SubmissionStage, self).validate()

        if not self.submissions:
            violations.add(ValidationMessage(
                ValidationMessage.ERROR,
                _(u"Submissions are not specified for {class_name} '{stage_title}'").format(
                    class_name=self.__class__.__name__, stage_title=self.display_name
                )
            ))

        return violations

    @property
    def has_all_submissions(self):
        return all(submission.upload is not None for submission in self.submissions)

    def submissions_view(self, context):
        fragment = Fragment()

        submission_contents = []
        for resource in self.submissions:
            resource_fragment = resource.render('submissions_view', context)
            fragment.add_frag_resources(resource_fragment)
            submission_contents.append(resource_fragment.content)

        context = {'stage': self, 'submission_contents': submission_contents}
        fragment.add_content(loader.render_template("templates/html/stages/submissions_view.html", context))

        return fragment


class ReviewBaseStage(BaseGroupActivityStage):
    type = u'Grade'
    STAGE_CONTENT_TEMPLATE = None

    @property
    def allowed_nested_blocks(self):
        blocks = super(ReviewBaseStage, self).allowed_nested_blocks
        blocks.update(OrderedDict([
            (GroupProjectReviewQuestionXBlock.CATEGORY, _(u"Review Question"))
        ]))
        return blocks

    @property
    def questions(self):
        return self._get_children_by_category(GroupProjectReviewQuestionXBlock.CATEGORY)

    @property
    def grade_questions(self):
        return (question for question in self._questions if question.grade)

    def validate(self):
        violations = super(ReviewBaseStage, self).validate()

        if not self.questions:
            violations.add(ValidationMessage(
                ValidationMessage.ERROR,
                _(u"Questions are not specified for {class_name} '{stage_title}'").format(
                    class_name=self.__class__.__name__, stage_title=self.display_name
                )
            ))

        return violations

    def get_stage_content_fragment(self, context):
        children_fragment = self.render_children_fragment(context)

        fragment = Fragment()
        fragment.add_frag_resources(children_fragment)
        render_context = {'stage': self, 'children_content': children_fragment.content}
        fragment.add_content(loader.render_template(self.STAGE_CONTENT_TEMPLATE, render_context))
        return fragment


class PeerReviewStage(ReviewBaseStage):
    STAGE_CONTENT_TEMPLATE = 'templates/html/stages/peer_review.html'
    CATEGORY = 'group-project-v2-stage-peer-review'

    @property
    def allowed_nested_blocks(self):
        blocks = super(PeerReviewStage, self).allowed_nested_blocks
        blocks.update(OrderedDict([
            (PeerSelectorXBlock.CATEGORY, _(u"Teammate selector"))
        ]))
        return blocks


class GroupReviewStage(ReviewBaseStage):
    STAGE_CONTENT_TEMPLATE = 'templates/html/stages/group_review.html'
    CATEGORY = 'group-project-v2-stage-group-review'

    @XBlock.handler
    def other_submission_links(self, request, suffix=''):
        pass
        # group_id = request.GET["group_id"]
        #
        # self.update_submission_data(group_id)
        # context = {'submissions': self.submissions}
        # html_output = loader.render_template('/templates/html/review_submissions.html', context)
        #
        # return webob.response.Response(body=json.dumps({"html": html_output}))


class AssessmentBaseStage(BaseGroupActivityStage):
    type = u'Evaluation'
    HTML_TEMPLATE = 'templates/html/stages/peer_assessment.html'

    def allowed_nested_blocks(self):
        blocks = super(AssessmentBaseStage, self).allowed_nested_blocks
        blocks.update(OrderedDict([
            (GroupProjectReviewAssessmentXBlock.CATEGORY, _(u"Review Question"))
        ]))
        return blocks

    @property
    def assessments(self):
        return self._get_children_by_category(GroupProjectReviewAssessmentXBlock.CATEGORY)

    def validate(self):
        violations = super(AssessmentBaseStage, self).validate()

        if not self.assessments:
            violations.add(ValidationMessage(
                ValidationMessage.ERROR,
                _(u"Assessments are not specified for {class_name} '{stage_title}'").format(
                    class_name=self.__class__.__name__, stage_title=self.display_name
                )
            ))

        return violations


class PeerAssessmentStage(AssessmentBaseStage):
    STAGE_CONTENT_TEMPLATE = 'templates/html/stages/peer_assessment.html'
    CATEGORY = 'group-project-v2-stage-peer-assessment'


class GroupAssessmentStage(AssessmentBaseStage):
    STAGE_CONTENT_TEMPLATE = 'templates/html/stages/group_assessment.html'
    CATEGORY = 'group-project-v2-stage-group-assessment'

