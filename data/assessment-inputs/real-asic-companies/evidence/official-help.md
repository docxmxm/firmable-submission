# Company Dataset - Help File

Publisher: Australian Securities & Investments Commission (ASIC).

> Transcription provenance (editorial, not publisher instructions): This is a complete, organized Markdown rendition of the retained publisher PDF, including all substantive prose, dictionary entries, examples, import instructions, change-log entries and hyperlink destinations. The PDF is dated 11/07/2025 3:11 PM and has 13 physical pages: a cover plus printed pages 1-12. Table rows split across PDF pages have been joined without changing their meaning. Repeated logos, headers and footers are represented once; screenshots are described below. Original wording, including apparent typos, is retained. Form-feed characters separate Markdown sections for bounded document lookup; section numbers returned by text lookup are not physical PDF page numbers. Physical page references appear in section headings. This is prepared source documentation, not a generated mapping, an expected canonical answer, or evidence of first-time unseen-source generalisation.
>
> PDF SHA256: `bce899f546b11dbf9e81cf7efa2fc105624f83e3608bfa778f3dc0a4c071896d`; size: 466372 bytes. Original PDF, Poppler TXT and retrieval receipt are retained together in `data/assessment-inputs/source-notices/asic-official-documents/`. Model input contains only this Markdown rendition of the help document.
>
> Source URL: https://data.gov.au/data/dataset/7b8656f9-606d-4337-af29-66b89b2eeefb/resource/9430b6b2-b28c-4b1a-a58a-a88b54912853/download/company-dataset-help-file.pdf

## Cover (physical page 1)

Company Dataset

HELP FILE

The cover displays the Australian Government coat of arms and ASIC branding.

## Table of Contents (physical page 2; printed page 1)

| Section | Printed page |
| --- | --- |
| Getting Started | 2 |
| 1. Purpose | 2 |
| 2. Company Register | 2 |
| 2.1. File Format | 2 |
| 2.2. File interaction | 3 |
| 2.3. Dataset Fields | 3 |
| 3. ASIC Connect Search | 4 |
| 4. Contact | 4 |
| Appendices | 5 |
| APPENDIX ITEM 1 | 5 |
| Company Register - MS Access Import Guide | 5 |
| APPENDIX ITEM 2 | 7 |
| Company Register - Data Dictionary | 7 |
| Dataset Change Log | 11 |

Repeated footer on printed pages 1-12: "Once printed this document is an uncontrolled copy"; "Company Dataset - Help File"; "Page [n] of 12"; "11/07/2025 3:11 PM".

## Getting Started (physical page 3; printed page 2)

### 1. Purpose

ASIC successfully made its Company register extract available to the public via data.gov.au on the 3 September 2014. This document is to provide detailed context and understanding of the files uploaded to Data.gov.au by ASIC every Tuesday.

### 2. Company Register

Select data from the ASIC's Company Register will be uploaded every Tuesday to www.data.gov.au. The data made available will be a snapshot of the register at a point in time. As a result, the information might not be accurate at the time you check this dataset. [ASIC Connect](https://connectonline.asic.gov.au/RegistrySearch/) updates information in real time, so please consider accessing information on that platform if you need precise information. Legislation prescribes the type of information ASIC is allowed to disclose to the public.

The company register dataset contains the following:

- Select fields that are publicly available (see data dictionary for field details);
- All currently registered companies that are able to be upload to data.gov.au;
- Deregistered companies for the past year; and
- Historical name data for registered companies.

### 2.1. File Format

The company dataset is provided in a file with the extension of CSV (comma separated values) and ZIP that contains the CSV file inside.

Important note: The data contained within the company register csv is not separated by commas

The company register csv uses the 'TAB' character as the delimiter (value separator) between values in the dataset; the 'TAB' character is used in place of a comma separator because company names can contain commas in their name and therefore is not a suitable delimiter for this dataset.

## File example and interaction (physical page 4; printed page 3)

An example of the data in the company register csv is as follows

```text
Company Name ACN             Type      Class    Sub Class Status Date of Registration         Date of Deregistration
           Previous State of Registration        State Registration number Modified since last report Current Name
 Indicator ABN      Current Name        Current Name Start Date

 COMPANY XYZ          987654321       APTY            LMSH          PNPC   REGD   08/01/2024
                      Y       44444444444

 COMPANY ABC   012345678                   APTY       LMSH    PROP         DRGD   19/04/2022   31/01/2024       VIC
       C1234567W                           Y          12345678989
```

### 2.2. File interaction

The company register data set contains over 3 million records. Because of the number of records contained in the file, common programs used to view data from CSV's (MS Excel, notepad, etc.) will not work.

Example: Most versions of MS Excel limits the number of rows to approximately 1 million rows, meaning if that dataset is viewed in Excel, less than half of the actual number of records will be available.

It is recommended users interact with the dataset on data.gov.au via one of the following methods:

Important note: ZIP file extensions cannot be access via an API. XLSX file extensions is no longer supported by CKAN as of December 2021 and any information extracted using this method may not return up to date data. For more information, see the [CKAN website](http://docs.ckan.org/en/latest/maintaining/datastore.html).

- Dataset Preview on data.gov.au;
- Access resource data via a web API with powerful query support (API information can be found in the [main CKAN Data API and DataStore documentation](http://docs.ckan.org/en/latest/maintaining/datastore.html)); or
- Import csv into an appropriate database (MS Access, Oracle, etc).

A step-by-step data import guide to importing Company data into MS Access can be found in the appendix of this help file. (APPENDIX ITEM 1)

### 2.3. Dataset Fields

The Company dataset contains the following fields:

- Company Name
- Australian Company Number (ACN)
- Type

## Dataset Fields (continued), search and contact (physical page 5; printed page 4)

- Class
- Sub Class
- Status
- Date of Registration
- Date of Deregistration
- Previous State of Registration (where applicable)
- State Registration Number (where applicable)
- Modified since last report – flag to indicate if data has been modified since last report
- Current Name Indicator
- Australian Business Number (ABN)
- Current Name
- Current Name Start Date

A comprehensive description of each field in the dataset is provided in the attached data dictionary (APPENDIX ITEM 2).

### 3. ASIC Connect Search

An alternative to searching the data contained in the Company Register extract is to use [ASIC Connect](https://connectonline.asic.gov.au/RegistrySearch/).

We have two services currently available through ASIC Connect:

- Search company and other registers
- Search business names registers

You can view publicly available information and purchase extracts on entities such as companies, business name, banned & disqualified or check name availability.

### 4. Contact

Any further enquiries can be directed to ASIC via email at Access.Request@asic.gov.au

## Appendices - APPENDIX ITEM 1 (physical pages 6-7; printed pages 5-6)

### Company Register - MS Access Import Guide

Use the following steps to import csv data into a new MS Access database; similar steps can be used to update an existing database:

Steps are provided using MS Access 2010

1. Download CSV dataset file;
2. Create blank database in MS Access;
3. Select 'Text File' from the Import & Link section of the External Data tab;
4. Browse to the CSV dataset file and choose to import into a new table and select 'Next';
5. Select Delimited format and select 'Next';
6. Choose the 'Tab' delimiter for your fields, and check the box indicating the first row contains Field Names. Select 'Next';
7. Select Next to accept field names as is;
8. Decide on how a primary key option (this decision may change depending on use), select 'Next';
9. Give the table a name and select 'Finish'.

Screenshot transcription (editorial description of the original figures): The first screenshot highlights External Data > Import & Link > Text File. The next screenshot shows `SourceFile.csv` and selects "Import the source data into a new table in the current database", rather than appending to an existing table. The Import Text Wizard screenshot on printed page 6 selects `Tab`, checks `First Row Contains Field Names`, and displays `Text Qualifier: (none)`. Its preview includes the columns Company Name, ACN and Type. These are illustrative wizard screenshots, not additional business records in the dataset.

## APPENDIX ITEM 2 - Company Register - Data Dictionary: Identity and company type (physical page 8; printed page 7)

| Field | Description | Data Type | Data Parameters |
| --- | --- | --- | --- |
| Company Name | Company name as it appears on the register | Text | As appears on the ASICs register |
| ACN | Australian Company Number | Text | Format: 9 digit numerical including leading zeros (e.g. 012345678) *Exceptions: ARBN – Instances where the Type = 'RACN' the value contained in the ACN field is actually the Australian Registered Body Number (ARBN). Registrable Australian Bodies carrying on, or wishing to carry on, business interstate are registered on a national basis and each is allotted an ARBN in the same way that registered companies are issued with an ACN. |
| Type | Specifies the type of company | Text | Values for this field are limited to the following: <br> APTY - Australian proprietary company <br> APUB - Australian public company <br> FNOS - Foreign company (a company incorporated outside Australia but registered as a foreign company in Australia) <br> RACN - Registered Australian Corporation (an organisation incorporated in Australia under a law other than the Corporations Law but required to be registered under the Corporations Law) |

## APPENDIX ITEM 2 - Company Register - Data Dictionary: Company class and sub-class (physical pages 8-10; printed pages 7-9)

| Field | Description | Data Type | Data Parameters |
| --- | --- | --- | --- |
| Class | Specifies the class of a company | Text | Values for this field are limited to the following: <br> LMSH (Limited by Shares) - The liability of the members is limited to the amount unpaid on their shares. Shareholders are not required to contribute any further monies (in the case of a winding up) if the shares they have taken up are fully paid. <br> LMGT (Limited by Guarantee) - The members' liability is limited to a certain amount that they undertake to contribute in case of a winding up. The amount is specified in a clause in the Memorandum of Association of the company. A member of a company limited by guarantee is not required to in any capital while the company is a going concern. <br> LMSG (Limited by Both Shares and Guarantees) - The member has the liability as a shareholder (to the extent of the amount unpaid on his shares) and as a guarantor (to the amount nominated in the Memorandum) in the event of a winding up. <br> NLIA (No Liability Company) - Mining companies only. No legal obligations on the shareholder to pay calls on their shares, e.g. Sterling Silver NL. <br> UNLM (Unlimited Company) - Is formed on the principle that there is no limit on the liability of the members. Simply, an incorporated partnership, e.g. Mercantile Services Pty, Solicitors. <br> NONE - Does not have an equivalent Australian liability |
| Sub Class | Specifies the sub-class of companies | Text | Values for this field are limited to the following: <br> EXPT - Exempt Proprietary Company. <br> HUNT (Proprietary home unit company) - A company that operates for the sole purpose of administering the day-to-day running of a residential property (refer to paragraph (c) of the definitions of 'special purpose company' in the Corporations (Fees) Regulations for full details). <br> LISN - Company licensed under Section 383 of the Corporations Act 2001 to omit 'Limited' from its name. <br> LISS - Company licensed under Section 383 to omit 'Limited' from its name - superannuation trustee company*. <br> LIST - Listed public company. <br> NEXT - Non-Exempt Proprietary Company. <br> NLTD - Non-profit public company registered without ‘Limited’ in its name under Section 150. <br> NONE - Unknown. <br> PNPC - Proprietary non-profit company. <br> PROP - Proprietary other. <br> PSTC - Proprietary superannuation trustee company. <br> PUBF - Foreign company required to lodge a balance sheet. <br> RACA - Registrable Australian corporation – association. <br> RACO - Registrable Australian corporation - non association. <br> STFI - Public company – small transferring financial institution. <br> ULSN - Unlisted public - non-profit company. <br> ULSS - Unlisted public - superannuation trustee company. <br> ULST - Unlisted pubic company. <br> *Superannuation Trustee Companies - Operates for the sole purpose of acting as a trustee of a regulated superannuation fund. This class of company may only be used on Annual Returns for 1994 or later. |

## APPENDIX ITEM 2 - Company Register - Data Dictionary: Status and remaining fields (physical pages 10-12; printed pages 9-11)

| Field | Description | Data Type | Data Parameters |
| --- | --- | --- | --- |
| Status | Status of company | Text | Values for this field are limited to the following: <br> DRGD - De-registered. <br> EXAD - External administration (in receivership/liquidation). <br> NOAC - Not active. <br> NRGD - Not registered. <br> PROV - Provisional (mentioned only under charges and refers to those which have not been fully registered). <br> REGD – Registered. <br> SOFF - Strike-off action in progress. <br> DISS - Dissolved by Special Act of Parliament. <br> DIV3 - Organisation Transferred Registration via DIV3. <br> PEND - Pending - Schemes. <br> CNCL - Cancelled. Transferred to another law or act. <br> *Registered under laws other than the Corporations Law. There are further variations used in connection with business names from State to State. Should you need help with any abbreviations not included in this list or further information on the meaning of any of these please contact us. |
| Date of Registration | Date on which a company was registered | Text | Date format: DD/MM/YYYY |
| Date of Deregistration | Date on which a company was deregistered | Text | Date format: DD/MM/YYYY |
| Previous State of Registration | State in which the company was originally registered | Text | State format: Abbreviated (e.g. Victoria = VIC) |
| State Registration Number | Registration number assigned when originally registered by the State | Text | The value format for this field will change depending on the State the number was generated. |
| Modified since last report | Flag indicating whether a dataset record has been updated since the last report | Text | Y / Null. An update includes the following: Company name Company type Company class Company sub class Company status |
| Current Name Indicator | Flag indicating whether the company name shown is the current company name | Text | Y / Null. |
| ABN | Australian Business Number | Text | ABN field has the following logic applied <br> IF Company has an ABN <br> THEN show ABN <br> ELSE show 0 This is the ABN for the company. |
| Current Name | Current company name | Text | Current name has the following logic applied <br> IF 'Current Name Indicator' = null <br> THEN show current company name for historical record <br> ELSE null |
| Current Name Start Date | Name start date for current registered company name | Text | Current name start date has the following logic applied <br> IF 'current name indicator' = null <br> THEN Show current company name start date <br> ELSE null |

## Dataset Change Log (physical pages 12-13; printed pages 11-12)

| Change | Date | Description |
| --- | --- | --- |
| Removed status code 'RSVD' | 05/04/2018 | Reserved names were removed from this dataset making this status code redundant. |
| Removed status code 'APPD' | 05/04/2018 | Reserved names were removed from this dataset making this status code redundant. |
| Removed type code 'BUSN' | 05/04/2018 | This type code was removed from the dataset. |
| Updated type field description | 12/04/2021 | Updated the information in the Data Dictionary for the Type field. |
| Updated help file | 18/05/2022 | Updated help file information. Added CNCL description to the Data Dictionary under Status. |
| Updated help file | 01/08/2023 | Updated help file information for ‘Modified since last report’ field. |
| Updated help file | 07/03/2025 | Updated help file information. Added Date of deregistration to the Data Dictionary. |
| Updated help file | 01/07/2025 | Updated Search information. |
