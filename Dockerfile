FROM python:2.7

MAINTAINER Ripperdoc

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY tools/ tools/
COPY fablr/ fablr/
COPY manage.py manage.py
COPY run.py run.py
RUN python manage.py lang_compile

ARG SOURCE_COMMIT=noversion  # provide from git or by Docker autobild
ARG SOURCE_BRANCH=''  # provide from git or by Docker autobild
ENV FABLR_VERSION ${SOURCE_BRANCH}-${SOURCE_COMMIT}
RUN echo \$FABLR_VERSION=${FABLR_VERSION}
CMD ["python","run.py"] # This format runs executable without bash/shell